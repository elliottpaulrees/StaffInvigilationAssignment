from flask import Flask, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, text
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.hybrid import hybrid_property
import sqlalchemy.pool
from flask import jsonify, render_template_string
from sqlalchemy import bindparam
import time

app = Flask(__name__)
app.secret_key = 'your-very-secret-key'

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://invigilationdb_loudatomup:2579baed22025cc45c8a56340c85426b33b4d69b@v5wu1.h.filess.io:61002/invigilationdb_loudatomup'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'poolclass': sqlalchemy.pool.QueuePool,
    'pool_size': 5,
    'max_overflow': 0,
}

db = SQLAlchemy(app)

# ─── MODELS ──────────────────────────────────────────────────────────────────────

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


# ─── HELPERS ─────────────────────────────────────────────────────────────────────

def get_model_by_week(week):
    return LessonsWeekA if week == 'A' else LessonsWeekB if week == 'B' else None

def valid_lesson_filters(model, day, period):
    return [
        or_(model.Class == 'free', model.Class.like('%11%'), model.Class.like('%13%')),
        model.Day == day,
        model.Period == period,
    ]

def get_priority(lesson):
    is_priority = any(year in lesson.Class for year in ['11', '13'])
    is_free = lesson.Class.lower() == 'free'
    return (
        0 if is_priority else 1 if is_free else 2,
        lesson.teacher.invigilation_count
    )

# ─── ROUTES ──────────────────────────────────────────────────────────────────────

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/find_lessons', methods=['POST'])
def find_lessons():
    # Get form data
    print("test")
    week = request.form.get('week')
    day = request.form.get('day')
    period = request.form.get('period')
    num_exams = int(request.form.get('num_exams'))
    
    # Store in session
    session.modified = True
    session['week'] = week
    session['period'] = period
    session['day'] = day
    
    # Validate week
    model = get_model_by_week(week)
    if not model:
        return "Invalid week selected.", 400

    # Prepare exam data
    exams = []
    total_invigilators_needed = 0
    
    for i in range(1, num_exams + 1):
        exams.append({
            'exam_name': request.form.get(f'exam_name_{i}'),
            'subject': request.form.get(f'subject_{i}'),
            'room': request.form.get(f'room_{i}'),
            'invigilators_required': int(request.form.get(f'invigilators_count_{i}'))
        })
        total_invigilators_needed += exams[-1]['invigilators_required']

    # Fetch ALL potential teachers (not just the ones we'll assign)
    lessons = (
        model.query.filter(*valid_lesson_filters(model, day, period))
        .options(joinedload(model.teacher).joinedload(Teachers.invigilation))
        .limit(total_invigilators_needed * 5)  # Get more than we need for swap options
        .all()
    )

    # Sort by priority (same logic as before)
    lessons.sort(key=get_priority)

    # Store ALL potential teachers in session for swaps
    all_potential_teachers = []
    seen_codes = set()
    
    for lesson in lessons:
        teacher = lesson.teacher
        if teacher and teacher.TeacherCode not in seen_codes:
            all_potential_teachers.append({
                'TeacherCode': teacher.TeacherCode,
                'FirstName': teacher.FirstName,
                'LastName': teacher.LastName,
                'TotalInvigilations': teacher.invigilation_count,
                'Class': lesson.Class,
                'Priority': 0 if '13' in lesson.Class else 1 if '11' in lesson.Class else 2
            })
            seen_codes.add(teacher.TeacherCode)
    
    session['all_potential_teachers'] = all_potential_teachers

    # Now select just the teachers we'll actually assign
    selected_teachers = []
    used_codes = set()
    
    for lesson in lessons:
        teacher = lesson.teacher
        if teacher and teacher.TeacherCode not in used_codes:
            selected_teachers.append({
                'teacher': teacher,
                'reason': lesson.Class
            })
            used_codes.add(teacher.TeacherCode)
        if len(selected_teachers) >= total_invigilators_needed:
            break

    # Assign to exams (original logic preserved)
    assignment_index = 0
    
    for exam in exams:
        exam['teachers'] = []
        for _ in range(exam['invigilators_required']):
            if assignment_index >= len(selected_teachers):
                break
            entry = selected_teachers[assignment_index]
            teacher = entry['teacher']
            reason = entry['reason']
            assignment_index += 1
            exam['teachers'].append({
                'TeacherCode': teacher.TeacherCode,
                'FirstName': teacher.FirstName,
                'LastName': teacher.LastName,
                'TotalInvigilations': teacher.invigilation_count,
                'reason': f"Covering during {reason}"
            })

    session['exam_assignments'] = exams
    return render_template('results.html', exams=exams, week=week, day=day, period=period, all_teachers=all_potential_teachers )

@app.route('/confirm_invigilators', methods=['POST'])
def confirm_invigilators():
 
    # First check for duplicate assignments
    teacher_codes = []
    for exam in session.get('exam_assignments', []):
        teacher_codes.extend(t['TeacherCode'] for t in exam['teachers'])
    
    if len(teacher_codes) != len(set(teacher_codes)):
        return render_template_string('''...'''), 400

    # Get all teacher codes to update in one query
    codes_to_update = list(set(teacher_codes))  # Unique codes
    
    # Single bulk update operation
  
    db.session.execute(
        text("""UPDATE invigilationsessions 
                SET Count = Count + 1 
                WHERE TeacherCode IN :codes""").bindparams(bindparam('codes', expanding=True)),
        {'codes': codes_to_update}
    )
    db.session.commit()

 
    return render_template('confirmation.html',
        exams=session.get('exam_assignments', []),
        day=session.get('day'),
        period=session.get('period'),
        week=session.get('week')
    )

@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()

@app.before_request
def before_request():
    # Reset any expired connections
    db.session.remove()

if __name__ == '__main__':
    app.run(debug=False)
