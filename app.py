from flask import Flask, render_template, request
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.orm import joinedload


app = Flask(__name__)

# Configure the database URI
#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql://root:qwertyui@localhost/invigilationdata'
#app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:qwertyui@localhost/invigilationdata'
# Configure the database URI to point to the cloud-based MySQL database
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://invigilationdb_loudatomup:2579baed22025cc45c8a56340c85426b33b4d69b@v5wu1.h.filess.io:61002/invigilationdb_loudatomup'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database
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
    TeacherID = db.Column(db.Integer, primary_key=True)
    FirstName = db.Column(db.String(50))
    LastName = db.Column(db.String(50))
    Code = db.Column(db.String(20))
    TotalInvigilations = db.Column(db.Integer)

    def __repr__(self):
        return f'<Teacher {self.FirstName} {self.LastName}>'

class LessonsWeekA(db.Model):
    __tablename__ = 'lessonsweeka'
    LessonID = db.Column(db.Integer, primary_key=True)
    TeacherIDA = db.Column(db.Integer, db.ForeignKey('teachers.TeacherID'))  # Foreign key to Teachers
    Day = db.Column(db.String(50))
    Period = db.Column(db.String(50))
    Subject = db.Column(db.String(50))
    Class = db.Column(db.String(100))

    # Define the relationship to the Teacher model
    teacher = db.relationship('Teachers', backref='lessons')

    def __repr__(self):
        return f'<Lesson {self.Subject} on {self.Day}>'

class LessonsWeekB(db.Model):
    __tablename__ = 'lessonsweekb'
    LessonID = db.Column(db.Integer, primary_key=True)
    TeacherIDB = db.Column(db.Integer, db.ForeignKey('teachers.TeacherID'))  # Foreign key to Teachers
    Day = db.Column(db.String(50))
    Period = db.Column(db.String(50))
    Subject = db.Column(db.String(50))
    Class = db.Column(db.String(100))

    # Define the relationship to the Teacher model
    teacher = db.relationship('Teachers', backref='lessons_week_b')

    def __repr__(self):
        return f'<Lesson {self.Subject} on {self.Day}>'

@app.route('/lessonsA')
def get_lessonsA():
    try:
        lessons = LessonsWeekA.query.all()
        return '<br>'.join([
            f"Teacher ID: {lesson.TeacherIDA}, Day: {lesson.Day}, "
            f"Period: {lesson.Period}, Subject: {lesson.Subject}, Class: {lesson.Class}"
            for lesson in lessons
        ])
    except Exception as e:
        return f"Error retrieving lessons: {str(e)}"

@app.route('/teachers')
def get_teachers():
    try:
        teachers = Teachers.query.all()
        return '<br>'.join([
            f"Teacher ID: {teacher.TeacherID}, First Name: {teacher.FirstName}, Last Name: {teacher.LastName}, Code: {teacher.Code}, Total Invigilations: {teacher.TotalInvigilations}"
            for teacher in teachers
        ])
    except Exception as e:
        return f"Error retrieving teachers: {str(e)}"

@app.route('/find_lessons', methods=['POST'])
def find_lessons():
    subject = request.form.get('subject')
    week = request.form.get('week')
    day = request.form.get('day')
    period = request.form.get('period')
    invigilators_count = int(request.form.get('invigilators'))
    exam_name = request.form.get('examName')

    print(f"Searching for {subject} lessons on week {week} {day} during {period}")

    try:
        # Choose the correct model based on the selected week
        if week == 'A':
            model = LessonsWeekA
        elif week == 'B':
            model = LessonsWeekB
        else:
            return "Invalid week selected."

        # Common filter conditions using the model class
        filters = [
            (model.Class == 'free') | (model.Class.like('%11%'))  | (model.Class.like('%13%')) ,
            model.Period == period,
            model.Subject != subject,
            model.Day == day
        ]

        # Get more lessons than needed to account for duplicate teachers
        buffer_multiplier = 3
        query_limit = invigilators_count * buffer_multiplier

        lessons = (model.query.filter(*filters)
                  .options(joinedload(model.teacher))
                  .limit(query_limit)
                  .all())

        # Sort lessons: prioritize those with Class containing '11', then by teacher's invigilation count
        lessons.sort(key=lambda lesson: ( not any(year in lesson.Class for year in ['11', '13']),  # True (1) for non-priority, False (0) for priority
        lesson.teacher.TotalInvigilations))  # Then sort by invigilation count))

        # Collect unique teachers until we have enough
        unique_teachers = set()
        results = []
        
        for lesson in lessons:
            if lesson.teacher and lesson.teacher.TeacherID not in unique_teachers:
                unique_teachers.add(lesson.teacher.TeacherID)
                results.append({
                    "TeacherID": lesson.teacher.TeacherID,
                    "Name": f"{lesson.teacher.FirstName} {lesson.teacher.LastName}",
                    "Code": lesson.teacher.Code,
                    "TotalInvigilations": lesson.teacher.TotalInvigilations,
                    "reason": lesson.Class
                })
                if len(results) >= invigilators_count:
                    break

        return render_template('results.html', 
                            results=results, 
                            exam_name=exam_name, 
                            subject=subject, 
                            week=week, 
                            day=day, 
                            period=period)
    except Exception as e:
        return f"Error retrieving lessons: {str(e)}"
    

@app.route('/confirm_invigilators', methods=['POST'])
def confirm_invigilators():
    # Extract selected teacher IDs from the form submission
    selected_ids = request.form.getlist('selected_teachers')

    if not selected_ids:
        return "No teachers were selected.", 400

    # Retrieve the exam details from the form
    exam_name = request.form.get('exam_name')
    subject = request.form.get('subject')
    day = request.form.get('day')
    period = request.form.get('period')

    # Query the database for the selected teachers
    confirmed_teachers = Teachers.query.filter(Teachers.TeacherID.in_(selected_ids)).all()

    # Increment each teacher's invigilation count
    for teacher in confirmed_teachers:
        teacher.TotalInvigilations = teacher.TotalInvigilations + 1  # handles null

    db.session.commit()  # save the changes

    # Pass the exam details and confirmed teachers to the template
    return render_template(
        'confirmation.html',
        exam_name=exam_name,
        subject=subject,
        day=day,
        period=period,
        teachers=confirmed_teachers
    )

if __name__ == '__main__':
    app.run(debug=True)