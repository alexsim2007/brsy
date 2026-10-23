"""Clearly marked fixture data. Never used as fallback for real BRS requests."""
import time


def dashboard():
    now = time.time()
    subjects = []
    rows = [
        ("Алгоритмы и структуры данных", 86, 90, "exam", "Иванова А. В."),
        ("Математический анализ", 64, 80, "exam", "Петров Д. С."),
        ("Основы программирования", 92, 100, "grading_credit", "Смирнов И. А."),
        ("Линейная алгебра", 48, 75, "exam", "Кузнецова Е. Н."),
        ("Английский язык", 78, 85, "credit", "Соколова М. В."),
        ("История России", 80, 100, "credit", "Орлов А. Д."),
    ]
    for i, (name, score, available, kind, teacher) in enumerate(rows):
        subjects.append({"id": str(i + 1), "name": name, "score": score, "available": available,
                         "type": kind, "teacher": teacher, "semester_id": "2026-1", "group": "КТбо2-3", "faculty": "ИКТИБ"})
    return {"demo": True, "linked": True, "name": "Александр", "subjects": subjects,
            "semesters": [{"id": "2026-1", "title": "Осень 2026"}], "semester": "2026-1", "updated": now,
            "settings": {"notifications": True, "target": 80}, "error": None,
            "stats": {"average": 74.7, "count": 6, "at_target": 3},
            "history": [
                {"id": 3, "subject_id": "1", "subject": rows[0][0], "old": 78, "new": 86, "created": now - 1800},
                {"id": 2, "subject_id": "3", "subject": rows[2][0], "old": 82, "new": 92, "created": now - 7200},
                {"id": 1, "subject_id": "2", "subject": rows[1][0], "old": 60, "new": 64, "created": now - 86400},
            ]}


def detail(sid):
    subject = next((s for s in dashboard()["subjects"] if s["id"] == sid), None)
    if not subject:
        return None
    subject.update(modules=[{"title": "Модуль 1 · Основы", "works": [
        {"id": "w1", "title": "Практическая работа № 1", "score": 18, "max": 20, "date": "2026-09-15"},
        {"id": "w2", "title": "Контрольная работа", "score": 22, "max": 25, "date": "2026-10-01"},
    ]}, {"title": "Модуль 2 · Практика", "works": [
        {"id": "w3", "title": "Проектная работа", "score": max(0, subject["score"] - 40), "max": 55, "date": "2026-10-21"},
    ]}], extra=0, journal={"lessons": [
        {"LessonDate": "2026-10-21", "LessonType": "Практика", "Attended": True},
        {"LessonDate": "2026-10-14", "LessonType": "Лекция", "Attended": False},
        {"LessonDate": "2026-10-07", "LessonType": "Лекция", "Attended": True}], "gym": None}, journal_error=None)
    return subject
