from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session,
    # flask_sqlalchemy,shapely.geometry
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from shapely.geometry import Point, Polygon
import csv
import json, re, os
from io import StringIO
from werkzeug.utils import secure_filename
import math
import uuid

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///attendance.db'
app.config['SECRET_KEY'] = 'change‑me'
app.config['UPLOAD_FOLDER'] = 'selfies'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

# Admin model
class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
#
#  models
#
class Venue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    # store list of [lat,lng] pairs as JSON
    boundary = db.Column(db.Text, nullable=False)
    courses = db.relationship('Course', backref='venue', lazy=True)


class Course(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    department = db.Column(db.String(80))
    venue_id = db.Column(db.Integer, db.ForeignKey('venue.id'))
    lecture_time = db.Column(db.String(50))  # e.g. "10:00-11:00"
    lecture_number = db.Column(db.String(20))  # e.g. "3rd", "4th"

    access_token = db.Column(db.String(36), unique=True, nullable=False,
                             default=lambda: str(uuid.uuid4()))
    active = db.Column(db.Boolean, nullable=False, default=True)

class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    name = db.Column(db.String(120), nullable=False)
    index_number = db.Column(db.String(8), nullable=False)
    selfie = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    course_id = db.Column(db.Integer,
                          db.ForeignKey('course.id'))
    venue_id = db.Column(db.Integer,
                         db.ForeignKey('venue.id'))
    course = db.relationship('Course', backref='attendances', lazy=True)
    venue = db.relationship('Venue', backref='attendances', lazy=True)

#
#  helpers
#
def validate_email_name(email: str, fullname: str) -> bool:
    local = email.split('@')[0].lower()
    first = fullname.split()[0].lower()
    return first in local

def meters_between(lat1, lng1, lat2, lng2):
    R = 6371000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    Δφ = math.radians(lat2 - lat1)
    Δλ = math.radians(lng2 - lng1)
    a = math.sin(Δφ/2)**2 + math.cos(φ1)*math.cos(φ2)*math.sin(Δλ/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def point_in_polygon(lat: float, lng: float, boundary_json: str) -> bool:
    """Return True if (lat,lng) lies inside the polygon defined by
    a JSON string. If the JSON cannot be decoded or is invalid, return
    False.
    """
    try:
        b = json.loads(boundary_json)
    except Exception:
        return False

    # circle style: {"center":[lng,lat], "radius": meters}
    if isinstance(b, dict) and 'center' in b and 'radius' in b:
        clng, clat = b['center']
        return meters_between(lat, lng, clat, clng) <= float(b['radius'])

    # fallback polygon style (existing behavior)
    if not isinstance(b, list):
        return False

    point = Point(lng, lat)
    for candidate in (b, [[c[1], c[0]] for c in b]):
        try:
            poly = Polygon(candidate)
        except Exception:
            continue
        if poly.contains(point) or poly.touches(point):
            return True

    return False

def _ordinal_suffix(n: int) -> str:
    if 10 <= (n % 100) <= 20:
        return 'th'
    if n % 10 == 1:
        return 'st'
    if n % 10 == 2:
        return 'nd'
    if n % 10 == 3:
        return 'rd'
    return 'th'

def format_ordinal(n: int) -> str:
    return f"{n}{_ordinal_suffix(n)}"

def parse_ordinal(s: str) -> int:
    if not s:
        return 0
    m = re.match(r'\s*(\d+)', s)
    return int(m.group(1)) if m else 0

#
#  routes
#
@app.route('/')
def index():
    # no home page defined, redirect students to the attendance form
    return redirect(url_for('attend'))

@app.route('/admin/register', methods=('GET','POST'))
def admin_register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if Admin.query.filter_by(username=username).first():
            flash('Username already exists.')
            return redirect(url_for('admin_register'))
        admin = Admin(username=username)
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        flash('Admin account created. Please log in.')
        return redirect(url_for('admin_login'))
    return render_template('admin_register.html')

@app.route('/admin/login', methods=('GET','POST'))
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        admin = Admin.query.filter_by(username=username).first()
        if admin and admin.check_password(password):
            session['admin_id'] = admin.id
            flash('Logged in successfully.')
            return redirect(url_for('admin'))
        else:
            flash('Invalid username or password.')
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_id', None)
    flash('Logged out.')
    return redirect(url_for('admin_login'))

@app.route('/admin/forgot', methods=('GET','POST'))
def admin_forgot():
    if request.method == 'POST':
        username = request.form['username']
        new_password = request.form['new_password']
        admin = Admin.query.filter_by(username=username).first()
        if not admin:
            flash('Username not found.')
            return redirect(url_for('admin_forgot'))
        admin.set_password(new_password)
        db.session.commit()
        flash('Password reset successful. Please log in.')
        return redirect(url_for('admin_login'))
    return render_template('admin_forgot.html')

@app.route('/admin', methods=('GET','POST'))
def admin():
    if 'admin_id' not in session:
        flash('Please log in as admin.')
        return redirect(url_for('admin_login'))

    if request.method == 'POST':
        # adding a venue
        if 'venue_name' in request.form:
            name = request.form['venue_name']
            boundary = request.form.get('boundary')  # must come from the hidden field
            if not boundary:
                flash('Set a center location and radius before adding a venue.')
                return redirect(url_for('admin'))
            # optional: validate JSON
            try:
                json.loads(boundary)
            except Exception:
                flash('Boundary is not valid JSON.')
                return redirect(url_for('admin'))

            vn = Venue(name=name, boundary=boundary)
            db.session.add(vn)
            db.session.commit()
            flash('Venue added.')
        # adding a course
        if 'course_name' in request.form:
            cname = request.form['course_name']
            dept = request.form.get('department')
            venue_id = request.form.get('venue_id')
            ltime = request.form.get('lecture_time')

            # calculate next lecture number if not provided
            lnum_raw = request.form.get('lecture_number', '').strip()
            if not lnum_raw:
                existing = Course.query.filter_by(name=cname).all()
                max_num = 0
                for ex in existing:
                    max_num = max(max_num, parse_ordinal(ex.lecture_number))
                lnum = format_ordinal(max_num + 1)
            else:
                n = parse_ordinal(lnum_raw)
                lnum = format_ordinal(n if n > 0 else 1)

            cr = Course(name=cname, department=dept,
                        venue_id=venue_id,
                        lecture_time=ltime, lecture_number=lnum)
            db.session.add(cr)
            db.session.commit()
            flash(f'Course added ({lnum}).')

        return redirect(url_for('admin'))

    venues = Venue.query.all()
    courses = Course.query.all()
    # also collect any uploaded selfie filenames so admin can browse
    selfies = [a.selfie for a in Attendance.query.all() if a.selfie]
    return render_template('admin.html',
                           venues=venues, courses=courses,
                           selfies=selfies)


@app.route('/admin/attendance')
def attendance_list():
    # show all attendance records with related course/venue info
    records = Attendance.query.all()
    return render_template('attendance.html', records=records)

@app.route('/attend', methods=('GET','POST'))
def attend():
    if request.method == 'POST':
        course_id = int(request.form['course_id'])
        course = Course.query.get(course_id)
        if not course or not course.active:
            flash('This course is not currently active.')
            return redirect(url_for('attend'))

        email = request.form['email']
        name = request.form['name']
        idx = request.form['index']
        lat_raw = request.form.get('latitude', '').strip()
        lng_raw = request.form.get('longitude', '').strip()
        if not lat_raw or not lng_raw:
            flash('Location not captured. Please allow location access and try again.')
            return redirect(url_for('attend'))

        # Prevent duplicate attendance by email and course
        duplicate = Attendance.query.filter_by(email=email, course_id=course_id).first()
        if duplicate:
            flash('You have already signed attendance for this course.')
            return redirect(url_for('attend'))

        lat = float(lat_raw)
        lng = float(lng_raw)
        venue_id = request.form.get('venue_id')
        selfie = request.files.get('selfie')
        filename = None
        if selfie:
            filename = secure_filename(selfie.filename)
            selfie.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        att = Attendance(
            email=email, name=name, index_number=idx,
            latitude=lat, longitude=lng,
            course_id=course_id, venue_id=venue_id,
            selfie=filename
        )
        db.session.add(att)
        db.session.commit()
        flash('Attendance recorded.')
        return redirect(url_for('attend'))

    venues = Venue.query.all()
    courses = Course.query.all()
    return render_template('attend.html',
                           venues=venues, courses=courses)

@app.route('/attend/<token>', methods=('GET','POST'))
def attend_token(token):
    course = Course.query.filter_by(access_token=token).first_or_404()
    if not course.active:
        flash('This attendance link is not active.')
        return redirect(url_for('attend'))

    venues = Venue.query.all()
    return render_template('attend.html',
                           venues=venues,
                           courses=[course],
                           preselected_course_id=course.id)

@app.route('/admin/course/<int:course_id>/delete', methods=('POST',))
def delete_course(course_id):
    cr = Course.query.get_or_404(course_id)
    db.session.delete(cr)
    db.session.commit()
    flash('Course deleted.')
    return redirect(url_for('admin'))

@app.route('/admin/venue/<int:venue_id>/delete', methods=('POST',))
def delete_venue(venue_id):
    v = Venue.query.get_or_404(venue_id)
    db.session.delete(v)
    db.session.commit()
    flash('Venue deleted.')
    return redirect(url_for('admin'))

@app.route('/attendance/<int:attendance_id>')
def attendance_detail(attendance_id):
    att = Attendance.query.get_or_404(attendance_id)
    return render_template('attendance_detail.html', attendance=att)

@app.route('/admin/course/<int:course_id>/toggle', methods=('POST',))
def toggle_course_active(course_id):
    cr = Course.query.get_or_404(course_id)
    cr.active = not cr.active
    db.session.commit()
    flash(f'Course {"activated" if cr.active else "deactivated"}.')
    return redirect(url_for('admin'))

# QR code for attendance link
@app.route('/admin/course/<int:course_id>/qrcode')
def course_qrcode(course_id):
    import qrcode
    from io import BytesIO
    cr = Course.query.get_or_404(course_id)
    link = url_for('attend_token', token=cr.access_token, _external=True)
    qr = qrcode.make(link)
    buf = BytesIO()
    qr.save(buf, format='PNG')
    buf.seek(0)
    return app.response_class(buf.read(), mimetype='image/png')

# Download attendance as CSV
@app.route('/admin/attendance/download')
def download_attendance():
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Email', 'Index', 'Course', 'Venue', 'Latitude', 'Longitude', 'Selfie'])
    for att in Attendance.query.all():
        writer.writerow([
            att.name,
            att.email,
            att.index_number,
            att.course.name if att.course else '',
            att.venue.name if att.venue else '',
            att.latitude,
            att.longitude,
            att.selfie or ''
        ])
    output.seek(0)
    return app.response_class(
        output.read(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=attendance.csv'}
    )

# Download attendance for a specific course
@app.route('/admin/course/<int:course_id>/attendance/download')
def download_course_attendance(course_id):
    course = Course.query.get_or_404(course_id)
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Email', 'Index', 'Course', 'Venue', 'Latitude', 'Longitude', 'Selfie'])
    for att in Attendance.query.filter_by(course_id=course_id).all():
        writer.writerow([
            att.name,
            att.email,
            att.index_number,
            course.name,
            att.venue.name if att.venue else '',
            att.latitude,
            att.longitude,
            att.selfie or ''
        ])
    output.seek(0)
    return app.response_class(
        output.read(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=attendance_{course.name}.csv'}
    )

if __name__ == '__main__':
    # ensure database tables exist
    with app.app_context():
        db.create_all()
    # listen on all network interfaces so other devices can connect
    app.run(debug=True, host='0.0.0.0')     # use port 5000 by default

