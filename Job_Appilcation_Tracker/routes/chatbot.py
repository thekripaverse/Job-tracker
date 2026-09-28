import json
import urllib.request
from flask import Blueprint, request, jsonify, session, current_app
from routes.auth import login_required
from database.db import get_db
from routes.applications import format_application_row

chatbot_bp = Blueprint('chatbot', __name__)

@chatbot_bp.route('/api/chat', methods=['POST'])
@login_required
def chat():
    user_id = session.get('user_id')
    data = request.get_json() or {}
    user_message = data.get('message', '').strip()
    history = data.get('history', [])

    if not user_message:
        return jsonify({'error': 'Please provide a message.'}), 400

    api_key = current_app.config.get('GROQ_API_KEY', '').strip()

    if not api_key:
        fallback_msg = (
            "**Groq API Key Required**\n\n"
            "To activate your AI Career Assistant powered by **Qwen 2.5 (`qwen-2.5-32b`)**, please add your Groq API key to the `.env` file:\n"
            "```env\nGROQ_API_KEY=gsk_your_actual_key_here\n```\n\n"
            "**How to get a FREE key (1 minute):**\n"
            "1. Visit [console.groq.com/keys](https://console.groq.com/keys)\n"
            "2. Sign in with Google / GitHub\n"
            "3. Click **Create API Key** and copy your `gsk_...` key\n"
            "4. Paste it in `.env` and restart the server!"
        )
        return jsonify({
            'reply': fallback_msg,
            'api_key_missing': True
        })

    # Fetch User's Applications to build real-time context
    db = get_db()
    rows = db.execute('SELECT * FROM applications WHERE user_id = ? AND (archived = 0 OR archived IS NULL) ORDER BY last_updated DESC', (user_id,)).fetchall()
    apps = [format_application_row(r) for r in rows]

    app_context_lines = []
    for a in apps:
        line = f"- {a['company_name']} ({a['job_title']}): Status={a['status']}"
        if a.get('formatted_interview_date'):
            line += f", Interview={a['formatted_interview_date']}"
        if a.get('location'):
            line += f", Location={a['location']}"
        if a.get('salary'):
            line += f", Salary={a['salary']}"
        app_context_lines.append(line)

    app_context_str = "\n".join(app_context_lines) if app_context_lines else "No applications logged yet."

    # Fetch User's Synced Career Emails
    email_rows = db.execute('''
        SELECT em.sender_name, em.sender_email, em.subject, em.classification, em.received_at, em.extracted_data,
               a.company_name as app_company, a.job_title as app_role
        FROM email_messages em
        LEFT JOIN applications a ON em.matched_application_id = a.id
        WHERE em.user_id = ? AND (em.is_job_related IS NULL OR em.is_job_related = 1)
        ORDER BY em.received_at DESC
        LIMIT 10
    ''', (user_id,)).fetchall()

    email_context_lines = []
    for em in email_rows:
        cls_clean = em['classification'].replace('_', ' ').title()
        line = f"- [{cls_clean}] From: {em['sender_name'] or em['sender_email']} | Subject: \"{em['subject']}\" | Date: {em['received_at']}"
        if em['app_company']:
            line += f" | Matched: {em['app_company']} - {em['app_role']}"
        email_context_lines.append(line)

    email_context_str = "\n".join(email_context_lines) if email_context_lines else "No synced career emails yet."

    # Active resume context (server-resolved; user never needs to paste it).
    from services.resume_context import get_active_resume, build_resume_brief
    resume_ctx = get_active_resume(user_id)
    if resume_ctx['has_resume']:
        resume_context_str = (
            f"Active resume: {resume_ctx['resume_filename']} ({resume_ctx['version_name']}).\n"
            f"RESUME CONTENT (authoritative — base resume recommendations ONLY on this text):\n"
            f"{build_resume_brief(resume_ctx['resume_text'])}"
        )
    else:
        resume_context_str = "No active resume saved yet. If the user asks for resume feedback, invite them to upload one in FIT Score."

    system_prompt = (
        "You are an expert AI Career Coach & Interview Assistant embedded in the 'Job & Internship Tracker' app. "
        "Your goal is to empower the user in their job search, interview preparation, resume tuning, and follow-up emails.\n\n"
        "Here is the user's real-time job application portfolio:\n"
        f"{app_context_str}\n\n"
        "Here are the user's recent synced career/recruitment emails:\n"
        f"{email_context_str}\n\n"
        "Here is the user's active resume context:\n"
        f"{resume_context_str}\n\n"
        "Guidelines:\n"
        "1. Be direct, encouraging, practical, and highly relevant to their target companies and roles.\n"
        "2. When asked about interview prep, tailor questions specifically to the roles and companies in their tracker.\n"
        "3. When asked about recent emails, status updates, or recruiters to follow up with, answer accurately using the real synced email intelligence above.\n"
        "4. When asked about their resume, use the RESUME CONTENT above — NEVER ask the user to paste/upload a resume that is already active. If no resume is active, say so plainly and point to FIT Score upload.\n"
        "5. NEVER invent experience, projects, skills, employers, certifications, education, or achievements. Ground resume advice in the provided resume content.\n"
        "6. Keep formatting clean with short markdown sections, bullet points, and bold emphasis where helpful. Keep replies focused and complete.\n"
        "7. IMPORTANT: You must ONLY answer questions related to careers, job applications, interview preparation, resumes, and career emails. If the user asks about unrelated topics, politely decline and steer them back to career topics."
    )

    # Build full messages payload
    messages = [{'role': 'system', 'content': system_prompt}]
    for msg in history[-6:]:  # include up to last 6 turns for context
        if isinstance(msg, dict) and msg.get('role') in ('user', 'assistant') and msg.get('content'):
            messages.append({'role': msg['role'], 'content': msg['content']})

    messages.append({'role': 'user', 'content': user_message})

    configured_model = current_app.config.get('GROQ_MODEL', 'openai/gpt-oss-120b')
    models_to_try = [configured_model]
    for m in ['openai/gpt-oss-120b', 'qwen/qwen3.8-27b', 'groq/compound']:
        if m not in models_to_try:
            models_to_try.append(m)

    last_error_msg = ""
    for model_name in models_to_try:
        groq_payload = json.dumps({
            'model': model_name,
            'messages': messages,
            'temperature': 0.7,
            'max_tokens': 2048
        }).encode('utf-8')

        try:
            req = urllib.request.Request(
                'https://api.groq.com/openai/v1/chat/completions',
                data=groq_payload,
                headers={
                    'Authorization': f'Bearer {api_key}',
                    'Content-Type': 'application/json',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                },
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=12) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                reply = res_data['choices'][0]['message']['content']
                finish_reason = res_data['choices'][0].get('finish_reason')
                if finish_reason == 'length':
                    # Model hit the token budget mid-answer: say so cleanly
                    # instead of leaving a sentence cut off with no recourse.
                    reply = reply.rstrip() + (
                        "\n\n*(My reply was cut short by length limits — "
                        "press Regenerate for the complete answer.)*"
                    )
                return jsonify({'reply': reply, 'model': model_name})

        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='ignore')
            print(f"Groq API Error for model {model_name} ({e.code}): {error_body}")
            last_error_msg = error_body
            if e.code == 401:
                return jsonify({
                    'reply': "**Invalid Groq API Key**. Please check your `GROQ_API_KEY` in `.env` and verify it starts with `gsk_`."
                })
            # If 400 bad model, loop to try next fallback model in list
            continue
        except Exception as e:
            print(f"Chatbot Exception for model {model_name}: {e}")
            last_error_msg = str(e)
            continue

    return jsonify({
        'reply': f"Groq API Error. Details: {last_error_msg[:120] if last_error_msg else 'Could not query Groq AI model.'}"
    })
