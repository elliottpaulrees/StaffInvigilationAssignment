from flask import Flask, render_template, request, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, or_
from sqlalchemy.orm import joinedload
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.pool import NullPool
import time
from sqlalchemy import update
from flask import current_app
from sqlalchemy import create_engine

import sqlalchemy.pool  # Import sqlalchemy.pool to access QueuePool

app = Flask(__name__)
app.secret_key = 'your-very-secret-key'


#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:qwertyui@localhost/teachertimetables'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://invigilationdb_loudatomup:2579baed22025cc45c8a56340c85426b33b4d69b@v5wu1.h.filess.io:61002/invigilationdb_loudatomup'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

#app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
#    'poolclass': NullPool
#}



# Configure connection pooling using SQLALCHEMY_ENGINE_OPTIONS
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'poolclass': sqlalchemy.pool.QueuePool,  # Use QueuePool for connection pooling
    'pool_size': 10,                         # Number of connections to keep open
    'max_overflow': 5,                       # Allowable overflow connections
}

# Initialize SQLAlchemy with the Flask app
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


def get_priority(lesson):
    # True if it's a Y11 or Y13 lesson
    is_priority = any(year in lesson.Class for year in ['11', '13'])
    # True if it's a free lesson (lower priority than Y11/Y13)
    is_free = lesson.Class.lower() == 'free'
    return (
        0 if is_priority else 1 if is_free else 2,  # priority group
        lesson.teacher.invigilation_count           # tiebreaker
    )

@app.route('/find_lessons', methods=['POST'])
def find_lessons():
    form_data = parse_exam_form(request)
    model = get_model_by_week(form_data['week'])
    if not model:
        return "Invalid week selected."

    try:
        # Retrieve the lessons based on the filters provided
        lessons = (model.query.filter(*valid_lesson_filters(model, form_data['subject'], form_data['day'], form_data['period']))
                   .options(joinedload(model.teacher).joinedload(Teachers.invigilation))
                   .limit(form_data['invigilators_count'] * 3)  # Load enough records to account for possible exclusions
                   .all())

        # Sort the lessons by priority (if applicable)
        lessons.sort(key=get_priority)

        # Create a set to track unique teacher codes and avoid duplicates
        unique_codes = set()
        results = []  # This will hold the selected teachers' data

        # Loop through the lessons and filter for teachers that are free
        for lesson in lessons:
            teacher = lesson.teacher
            if teacher and teacher.TeacherCode not in unique_codes:
                unique_codes.add(teacher.TeacherCode)
                # Fetch the InvigilationCountID here
                invigilation_count_id = teacher.invigilation.InvigilationCountID if teacher.invigilation else None
                results.append({
                    "TeacherCode": teacher.TeacherCode,
                    "FirstName": teacher.FirstName,
                    "LastName": teacher.LastName,
                    "TotalInvigilations": teacher.invigilation_count,
                    "InvigilationCountID": invigilation_count_id,  # Include InvigilationCountID
                    "reason": lesson.Class  # The reason the teacher is needed (could be the class name)
                })
                
                # Stop once we have the required number of invigilators
                if len(results) >= form_data['invigilators_count']:
                    break

        # Store the selected teachers in the session for later use
        session['confirmed_teachers'] = results
        
        # Render the results template and pass the teacher data along
        return render_template('results.html', **form_data, results=results)
    
    except Exception as e:
        # Handle any exceptions and return an error message
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

        # Get the teachers from the session (no need to query again)
        confirmed_teachers = session.get('confirmed_teachers', [])

        # Flag each teacher as selected
        for teacher in confirmed_teachers:
            teacher['selected'] = teacher['TeacherCode'] in selected_codes  # Mark as selected

        # Create a list of mappings for bulk update including the primary key (InvigilationCountID)
        updates = [
            {"InvigilationCountID": teacher['InvigilationCountID'],  # Primary Key
             "TeacherCode": teacher['TeacherCode'], 
             "Count": int(teacher['TotalInvigilations']) + 1}
            for teacher in confirmed_teachers if teacher['selected']
        ]
        

        # Perform bulk update
        if updates:  # Only execute if there are updates
            db.session.bulk_update_mappings(InvigilationSession, updates)
            db.session.commit()

        write_end = time.time()
        print(f"⏱️ DB write took {write_end - write_start:.3f} seconds")

        # 🕒 Template Render Timing
        render_start = time.time()
        response = render_template('confirmation.html',
                                   exam_name=exam_name,
                                   subject=subject,
                                   day=day,
                                   period=period,
                                   teachers=confirmed_teachers)  # Use the already fetched teachers
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


@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()