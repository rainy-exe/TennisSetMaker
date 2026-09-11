from flask import Blueprint, request, jsonify, session, render_template, flash, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from . import db  # Import the db object from __init__.py
from flask import current_app
from app.models import User 
from flask_wtf.csrf import CSRFProtect
from app import csrf
from app.form import RegisterForm
import requests
# Create the blueprint
auth_bp = Blueprint('auth', __name__)


# Register endpoint
@auth_bp.route('/register', methods=['POST'])
@csrf.exempt
def register():
    data = request.get_json()
    email = data['email']
    password = data['password'] # Ensure this is hashed

    if User.query.filter_by(email=email).first():
        return jsonify({'message': 'Email already registered, click OK to redirect to login page'}), 409

    hashed_password = generate_password_hash(password)
    new_user = User(email=email, password_hash=hashed_password)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({'message': 'User registered successfully.'})

# Login endpoint
@auth_bp.route('/login', methods=['POST'])
@csrf.exempt
def login():
    data = request.get_json()
    email = data['email']
    password = data['password']

    user = User.query.filter_by(email=email).first()
    if user and check_password_hash(user.password_hash, password):
        session['user_id'] = user.id
        return jsonify({'message': 'Login successful.'})
    return jsonify({'message': 'Invalid credentials.'}), 401

# Logout endpoint
@auth_bp.route('/logout', methods=['GET'])
def logout():
    session.pop('user_id', None)
    flash('Logged out successfully.', 'info')  # Only flash on logout
    return redirect(url_for('auth.login_page'))

# Check login status
@auth_bp.route('/check-session')
def check_session():
    return jsonify({'logged_in': 'user_id' in session})

# Login page route
@auth_bp.route('/login-page', methods=['GET'])
def login_page():
    return render_template('login.html')  # Ensure login.html is in the templates/ directory

 # Signup page route
@auth_bp.route('/signup-page', methods=['GET'])
def signup_page():
    form = RegisterForm()
    return render_template('signup.html')  # Ensure signup.html is in the templates/ directory

# Bot checking route in signup page
@auth_bp.route('/api/signup', methods=['POST'])
def api_signup():
    recaptcha_response = request.json.get('recaptcha_token')
    secret_key = '6LfnCi8rAAAAAMHL8op0mE8gL-gXKyXjoLTuckbX'

    verify_url = 'https://www.google.com/recaptcha/api/siteverify' # URL to verify the reCAPTCHA response
    payload = {
        'secret': secret_key,
        'response': recaptcha_response
    }

    result = requests.post(verify_url, data=payload).json()

    if not result.get('success'):
        return jsonify({'message': 'reCAPTCHA failed. Try again.'}), 400

# Forgot password page route
@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()

        if not user:
            flash('No account with that email was found.', 'danger')
            return redirect(url_for('auth.forgot_password'))

        # Here you would normally send an email with a reset token
        flash('Password reset instructions have been sent to your email.', 'info')
        return redirect(url_for('auth.login_page'))

    return render_template('forgot_password.html')


# Home route
@auth_bp.route('/')
def home():
    return render_template('signup.html')  # signup.html should be in app/templates/
