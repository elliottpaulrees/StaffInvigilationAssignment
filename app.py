from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, or_
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.hybrid import hybrid_property

import time
from flask import current_app


app = Flask(__name__)


#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:qwertyui@localhost/teachertimetables'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://invigilationdb_loudatomup:2579baed22025cc45c8a56340c85426b33b4d69b@v5wu1.h.filess.io:61002/invigilationdb_loudatomup'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,       # Recycle stale connections
    'pool_pre_ping': True,     # Test connection before using it
    'pool_size': 5,            # Min number of DB connections to keep ready
    'max_overflow': 10         # Extra connections allowed above pool_size
}



db = SQLAlchemy(app)

@app.route('/')
def home():
    
    return render_template('index.html')

@app.route('/test_connection')
def test_connection():
    try:
        with db.engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        return "Database connection is valid!"
    except Exception as e:
        return f"Database connection failed: {str(e)}"

class Teachers(db.Model):
    __tablename__ = 'teachers'
    TeacherCode = db.Column(db.String(10), primary_key=True)
    FirstName = db.Column(db.String(50))
    LastName = db.Column(db.String(50))

    lessons_week_a = db.relationship('LessonsWeekA', back_populates='teacher')
    lessons_week_b = db.relationship('LessonsWeekB', back_populates='teacher')
    invigilation = db.relationship('InvigilationSession', back_populates='teacher', uselist=False)

    @hybrid_property
    def invigilation_count(self):
        return self.invigilation.Count if self.invigilation else 0

class LessonsWeekA(db.Model):
    __tablename__ = 'lessonsweeka'
    LessonID = db.Column(db.Integer, primary_key=True)
    TeacherCode = db.Column(db.String(10), db.ForeignKey('teachers.TeacherCode'))
    Subject = db.Column(db.String(100))
    Class = db.Column(db.String(100))
    Day = db.Column(db.String(50))
    Period = db.Column(db.String(50))
    Room = db.Column(db.String(100))

    teacher = db.relationship('Teachers', back_populates='lessons_week_a')

class LessonsWeekB(db.Model):
    __tablename__ = 'lessonsweekb'
    LessonID = db.Column(db.Integer, primary_key=True)
    TeacherCode = db.Column(db.String(10), db.ForeignKey('teachers.TeacherCode'))
    Subject = db.Column(db.String(100))
    Class = db.Column(db.String(100))
    Day = db.Column(db.String(50))
    Period = db.Column(db.String(50))
    Room = db.Column(db.String(100))

    teacher = db.relationship('Teachers', back_populates='lessons_week_b')

class InvigilationSession(db.Model):
    __tablename__ = 'invigilationsessions'
    InvigilationCountID = db.Column(db.Integer, primary_key=True)
    TeacherCode = db.Column(db.String(10), db.ForeignKey('teachers.TeacherCode'))
    Count = db.Column(db.Integer, default=0)

    teacher = db.relationship('Teachers', back_populates='invigilation')

def parse_exam_form(req):
    return {
        'subject': req.form.get('subject'),
        'week': req.form.get('week'),
        'day': req.form.get('day'),
        'period': req.form.get('period'),
        'invigilators_count': int(req.form.get('invigilators')),
        'exam_name': req.form.get('examName')
    }

def get_model_by_week(week):
    return LessonsWeekA if week == 'A' else LessonsWeekB if week == 'B' else None

def valid_lesson_filters(model, subject, day, period):
    return [
        or_(model.Class == 'free', model.Class.like('%11%'), model.Class.like('%13%')),
        model.Period == period,
        model.Subject != subject,
        model.Day == day
    ]

@app.route('/find_lessons', methods=['POST'])
def find_lessons():
    form_data = parse_exam_form(request)
    model = get_model_by_week(form_data['week'])
    if not model:
        return "Invalid week selected."

    try:
        lessons = (model.query.filter(*valid_lesson_filters(model, form_data['subject'], form_data['day'], form_data['period']))
                   .options(joinedload(model.teacher).joinedload(Teachers.invigilation))
                   .limit(form_data['invigilators_count'] * 3)
                   .all())

        lessons.sort(key=lambda l: (
            not any(year in l.Class for year in ['11', '13']),
            l.teacher.invigilation_count
        ))

        unique_codes = set()
        results = []
        for lesson in lessons:
            teacher = lesson.teacher
            if teacher and teacher.TeacherCode not in unique_codes:
                unique_codes.add(teacher.TeacherCode)
                results.append({
                    "TeacherCode": teacher.TeacherCode,
                    "Name": f"{teacher.FirstName} {teacher.LastName}",
                    "TotalInvigilations": teacher.invigilation_count,
                    "reason": lesson.Class
                })
                if len(results) >= form_data['invigilators_count']:
                    break

        return render_template('results.html', **form_data, results=results)
    except Exception as e:
        return f"Error retrieving lessons: {str(e)}"

@app.route('/confirm_invigilators', methods=['POST'])
def confirm_invigilators():
    overall_start = time.time()

    selected_codes = request.form.getlist('selected_teachers')
    if not selected_codes:
        return "No teachers were selected.", 400

    try:
        exam_name = request.form.get('exam_name')
        subject = request.form.get('subject')
        day = request.form.get('day')
        period = request.form.get('period')

        # 🕒 DB Write Timing
        write_start = time.time()

        # Fetch Teachers by TeacherCode
        confirmed_teachers = Teachers.query.filter(Teachers.TeacherCode.in_(selected_codes)).all()

        values = ", ".join([f"('{code}', 1)" for code in selected_codes])
        sql = f"""
            INSERT INTO invigilationsessions (TeacherCode, Count)
            VALUES {values}
            ON DUPLICATE KEY UPDATE Count = Count + 1;
        """
        db.session.execute(text(sql))
        db.session.commit()
        write_end = time.time()
        print(f"⏱️ DB write took {write_end - write_start:.3f} seconds")

        # 🕒 Teacher Fetch Timing (for display)
        fetch_start = time.time()
        confirmed_teachers = (
            Teachers.query
            .options(joinedload(Teachers.invigilation))
            .filter(Teachers.TeacherCode.in_(selected_codes))
            .all()
        )
        fetch_end = time.time()
        print(f"⏱️ Teacher fetch took {fetch_end - fetch_start:.3f} seconds")

        # 🕒 Template Render Timing
        render_start = time.time()
        response = render_template('confirmation.html',
                                   exam_name=exam_name,
                                   subject=subject,
                                   day=day,
                                   period=period,
                                   teachers=confirmed_teachers)
        render_end = time.time()
        print(f"⏱️ Template render took {render_end - render_start:.3f} seconds")

        # 🚀 Total Duration
        total_time = time.time() - overall_start
        print(f"🚀 Total request time: {total_time:.3f} seconds")

        return response

    except Exception as e:
        db.session.rollback()
        print("❌ Error confirming invigilators:", e)
        return f"Error confirming invigilators: {str(e)}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
