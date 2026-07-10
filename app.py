from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
import os
import json

app = Flask(__name__)
app.secret_key = "change-this-secret-key"  # غيّرها لأي قيمة سرية قبل النشر

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "quiz.db")
SEED_FILE = os.path.join(BASE_DIR, "questions_data.json")

ADMIN_PASSWORD = "admin123"

# ------------------ إعداد قاعدة البيانات ------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter TEXT DEFAULT '',
            text TEXT NOT NULL,
            qtype TEXT NOT NULL CHECK(qtype IN ('true_false', 'multiple_choice')),
            correct_answer TEXT NOT NULL,
            explanation TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS choices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            choice_text TEXT NOT NULL,
            FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()


def seed_if_empty():
    """يستورد الأسئلة من questions_data.json تلقائياً أول مرة فقط (إذا كانت قاعدة البيانات فاضية)."""
    if not os.path.exists(SEED_FILE):
        return

    conn = get_db()
    count = conn.execute("SELECT COUNT(*) AS c FROM questions").fetchone()["c"]
    if count > 0:
        conn.close()
        return

    with open(SEED_FILE, encoding="utf-8") as f:
        data = json.load(f)

    for item in data.get("tf", []):
        correct = "true" if item["a"] else "false"
        conn.execute(
            "INSERT INTO questions (chapter, text, qtype, correct_answer, explanation) VALUES (?, ?, 'true_false', ?, ?)",
            (item.get("ch", ""), item["q"], correct, item.get("note", ""))
        )

    for item in data.get("mc", []):
        opts = item["opts"]
        correct_index = item["a"]
        correct_text = opts[correct_index]
        cur = conn.execute(
            "INSERT INTO questions (chapter, text, qtype, correct_answer, explanation) VALUES (?, ?, 'multiple_choice', ?, '')",
            (item.get("ch", ""), item["q"], correct_text)
        )
        qid = cur.lastrowid
        for opt in opts:
            conn.execute(
                "INSERT INTO choices (question_id, choice_text) VALUES (?, ?)",
                (qid, opt)
            )

    conn.commit()
    conn.close()


# ------------------ الصفحات العامة ------------------

@app.route("/")
def index():
    conn = get_db()
    chapter_filter = request.args.get("chapter", "")
    type_filter = request.args.get("type", "")

    query = "SELECT * FROM questions"
    conditions = []
    params = []
    if chapter_filter:
        conditions.append("chapter = ?")
        params.append(chapter_filter)
    if type_filter in ("true_false", "multiple_choice"):
        conditions.append("qtype = ?")
        params.append(type_filter)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id ASC"

    questions = conn.execute(query, params).fetchall()

    chapters = conn.execute(
        "SELECT DISTINCT chapter FROM questions WHERE chapter != '' ORDER BY chapter"
    ).fetchall()

    result = []
    for q in questions:
        choices = None
        if q["qtype"] == "multiple_choice":
            choices = conn.execute(
                "SELECT * FROM choices WHERE question_id = ?", (q["id"],)
            ).fetchall()
        result.append({"question": q, "choices": choices})
    conn.close()

    return render_template(
        "index.html",
        items=result,
        chapters=[c["chapter"] for c in chapters],
        chapter_filter=chapter_filter,
        type_filter=type_filter
    )


@app.route("/submit", methods=["POST"])
def submit():
    conn = get_db()

    # نصحح فقط الأسئلة اللي كانت معروضة فعلياً (حسب الفلترة اللي أرسلها الفورم)
    question_ids = request.form.getlist("question_id")
    if question_ids:
        placeholders = ",".join("?" * len(question_ids))
        questions = conn.execute(
            f"SELECT * FROM questions WHERE id IN ({placeholders})", question_ids
        ).fetchall()
    else:
        questions = conn.execute("SELECT * FROM questions").fetchall()

    score = 0
    total = len(questions)
    details = []

    for q in questions:
        field_name = f"q_{q['id']}"
        user_answer = request.form.get(field_name, "").strip()
        correct = user_answer != "" and user_answer == q["correct_answer"]
        if correct:
            score += 1
        details.append({
            "text": q["text"],
            "chapter": q["chapter"],
            "user_answer": user_answer if user_answer else "(لم تتم الإجابة)",
            "correct_answer": q["correct_answer"],
            "is_correct": correct,
            "explanation": q["explanation"]
        })

    conn.close()
    return render_template("result.html", score=score, total=total, details=details)


# ------------------ صفحات الإدارة ------------------

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin_panel"))
        else:
            flash("كلمة المرور غير صحيحة")
    return render_template("admin_login.html")


@app.route("/admin/panel", methods=["GET", "POST"])
def admin_panel():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        text = request.form.get("text", "").strip()
        qtype = request.form.get("qtype")
        chapter = request.form.get("chapter", "").strip()

        if not text:
            flash("لازم تكتب نص السؤال")
            return redirect(url_for("admin_panel"))

        conn = get_db()

        if qtype == "true_false":
            correct_answer = request.form.get("tf_correct")
            explanation = request.form.get("explanation", "").strip()
            conn.execute(
                "INSERT INTO questions (chapter, text, qtype, correct_answer, explanation) VALUES (?, ?, 'true_false', ?, ?)",
                (chapter, text, correct_answer, explanation)
            )
        elif qtype == "multiple_choice":
            choices = [
                request.form.get("choice1", "").strip(),
                request.form.get("choice2", "").strip(),
                request.form.get("choice3", "").strip(),
                request.form.get("choice4", "").strip(),
            ]
            choices = [c for c in choices if c]
            correct_index = request.form.get("mc_correct")

            if len(choices) < 2:
                flash("لازم تكتب خيارين على الأقل")
                conn.close()
                return redirect(url_for("admin_panel"))

            correct_text = request.form.get(f"choice{correct_index}", "").strip()

            if not correct_text:
                flash("لازم تحدد الإجابة الصحيحة")
                conn.close()
                return redirect(url_for("admin_panel"))

            cur = conn.execute(
                "INSERT INTO questions (chapter, text, qtype, correct_answer, explanation) VALUES (?, ?, 'multiple_choice', ?, '')",
                (chapter, text, correct_text)
            )
            question_id = cur.lastrowid
            for choice_text in choices:
                conn.execute(
                    "INSERT INTO choices (question_id, choice_text) VALUES (?, ?)",
                    (question_id, choice_text)
                )

        conn.commit()
        conn.close()
        flash("تمت إضافة السؤال بنجاح")
        return redirect(url_for("admin_panel"))

    conn = get_db()
    questions = conn.execute("SELECT * FROM questions ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("admin_panel.html", questions=questions)


@app.route("/admin/delete/<int:question_id>", methods=["POST"])
def admin_delete(question_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    conn = get_db()
    conn.execute("DELETE FROM choices WHERE question_id = ?", (question_id,))
    conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    conn.commit()
    conn.close()
    flash("تم حذف السؤال")
    return redirect(url_for("admin_panel"))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("index"))


# نجهز قاعدة البيانات والاستيراد التلقائي عند تحميل الملف (يشتغل مع gunicorn وأيضاً مع python app.py)
init_db()
seed_if_empty()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
