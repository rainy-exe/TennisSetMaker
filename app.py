from datetime import date
from flask import Flask, render_template, request, redirect, url_for
from helpers import load_players_from_csv
import csv 
import io

app = Flask(__name__)

CSV_PATH = "NTC_ratings_data.csv"

PLAYERS_STORE = None


def rating_to_dots(rating):
    dots = round(rating)
    return max(1, min(7, dots))


def get_players_store():
    global PLAYERS_STORE
    if PLAYERS_STORE is None:
        raw_players = load_players_from_csv(CSV_PATH)
        PLAYERS_STORE = []
        for i, p in enumerate(raw_players, start=1):
            gender_label = 'Male' if p['gender'] == 'M' else 'Female' if p['gender'] == 'F' else p['gender']
            PLAYERS_STORE.append({
                'id': i,
                'name': p['name'],
                'rating': p['rating'],
                'rating_dots': rating_to_dots(p['rating']),
                'gender': gender_label,
                'notes': '',
                'court_preference': '',
            })
    return PLAYERS_STORE


def find_player(player_id):
    return next((p for p in get_players_store() if p['id'] == player_id), None)


@app.route('/players')
def players():
    player_list = get_players_store()

    selected_id = request.args.get('selected', type=int)
    selected_raw = find_player(selected_id) if selected_id else None
    if selected_raw is None:
        selected_raw = player_list[0] if player_list else None

    selected = None
    if selected_raw:
        selected = dict(selected_raw)
        selected['initials'] = ''.join(w[0] for w in selected['name'].split())[:2].upper()
        selected['member_since'] = 'Unknown'
        selected['frequent_partners'] = []
        selected['recent_history'] = []

    return render_template(
        'players.html',
        active_page='players',
        players=player_list,
        selected_player=selected,
        duplicate_player=None,
    )


@app.route('/players/<int:player_id>/edit', methods=['GET', 'POST'])
def edit_player(player_id):
    player = find_player(player_id)
    if player is None:
        return redirect(url_for('players'))

    if request.method == 'POST':
        player['name'] = request.form.get('name', player['name']).strip()
        player['gender'] = request.form.get('gender', player['gender'])
        player['notes'] = request.form.get('notes', player.get('notes', '')).strip()
        player['court_preference'] = request.form.get('court_preference', player.get('court_preference', ''))
        try:
            new_rating = int(request.form.get('rating', player['rating']))
            player['rating'] = new_rating
            player['rating_dots'] = rating_to_dots(new_rating)
        except ValueError:
            pass
        return redirect(url_for('players'))

    return render_template('editplayer.html', active_page='players', player=player)

@app.route('/players/add', methods=['GET', 'POST'])
@app.route('/players/add', methods=['GET', 'POST'])
def add_player():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        gender = request.form.get('gender', 'Male')

        errors = []
        if not name:
            errors.append('Name is required.')
        try:
            rating = int(request.form.get('rating', ''))
        except ValueError:
            rating = None
            errors.append('Rating must be a number.')

        if errors:
            return render_template('addplayer.html', active_page='players', errors=errors, form=request.form)

        store = get_players_store()
        new_id = max((p['id'] for p in store), default=0) + 1
        store.append({
            'id': new_id,
            'name': name,
            'rating': rating,
            'rating_dots': rating_to_dots(rating),
            'gender': gender,
            'notes': request.form.get('notes', '').strip(),
            'court_preference': request.form.get('court_preference', ''),
        })

        return redirect(url_for('players', selected=new_id))

    return render_template('addplayer.html', active_page='players', errors=[], form={})

@app.route('/players/<int:player_id>/delete', methods=['POST'])
def delete_player(player_id):
    store = get_players_store()
    global PLAYERS_STORE
    PLAYERS_STORE = [p for p in store if p['id'] != player_id]
    return redirect(url_for('players'))

@app.route('/courts')
def courts():
    return render_template('courts.html', active_page='courts')


@app.route('/courts/add')
def add_court():
    return render_template('addcourt.html', active_page='courts')

@app.route('/templates')
def templates():
    return render_template('templates.html', active_page='templates')


@app.route('/templates/add')
def add_template():
    return render_template('addtemplate.html', active_page='templates')

@app.route('/templates/edit')
def edit_template():
    return render_template('edittemplate.html', active_page='templates')

@app.route('/sessions')
def create_session():
    return render_template('create-session.html', active_page='sessions')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html', active_page='dashboard')

TEAM_MEMBERS_STORE = []

CLUB_STORE = {}


TEAM_MEMBERS_STORE = [
    {'id': 1, 'name': 'Lena Nguyen', 'email': 'lenabnnguyen@gmail.com', 'role': 'Owner', 'joined': '23 Aug 2026'},
]

CLUB_STORE = {}

ROLE_INFO = {
    'Owner': [
        'Full club access.',
        'Can manage players and accounts.',
        'Can invite and remove admins and staff.',
    ],
    'Admin': [
        'Can manage players, courts and sessions.',
        'Can invite and remove staff.',
        'Cannot manage the owner account.',
    ],
    'Staff': [
        'Can view club information and add players.',
        'Cannot delete players.',
        'Cannot manage accounts.',
    ],
}

ASSIGNABLE_ROLES = ('Admin', 'Staff')   # Owner is never assigned or invited


def find_team_member(member_id):
    return next((m for m in TEAM_MEMBERS_STORE if m['id'] == member_id), None)


@app.route('/settings')
def settings():
    return render_template(
        'settings.html',
        active_page='settings',
        team_members=TEAM_MEMBERS_STORE,
        team_seat_limit=5,
        club=CLUB_STORE,
    )


@app.route('/settings/club', methods=['POST'])
def update_club():
    CLUB_STORE['name'] = request.form.get('club_name', '').strip()
    CLUB_STORE['contact_email'] = request.form.get('contact_email', '').strip()
    CLUB_STORE['contact_phone'] = request.form.get('contact_phone', '').strip()
    CLUB_STORE['address'] = request.form.get('address', '').strip()
    return redirect(url_for('settings'))


@app.route('/settings/team/<int:member_id>', methods=['GET', 'POST'])
def settings_member(member_id):
    member = find_team_member(member_id)
    if member is None:
        return redirect(url_for('settings'))

    error = None

    if request.method == 'POST':
        action = request.form.get('action')

        if member['role'] == 'Owner':
            error = 'The owner account cannot be changed or removed here.'
        elif action == 'remove':
            TEAM_MEMBERS_STORE.remove(member)
            return redirect(url_for('settings'))
        elif action == 'update_role':
            new_role = request.form.get('role')
            if new_role in ASSIGNABLE_ROLES:
                member['role'] = new_role
            return redirect(url_for('settings_member', member_id=member_id))

    return render_template(
        'settings_member.html',
        active_page='settings',
        member=member,
        role_info=ROLE_INFO,
        error=error,
    )


@app.route('/settings/team/invite', methods=['GET', 'POST'])
def invite_member():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'Staff')

        errors = []
        if not name:
            errors.append('Name is required.')
        if not email:
            errors.append('Email is required.')
        if role not in ASSIGNABLE_ROLES:
            errors.append('Please select a role.')

        if errors:
            return render_template(
                'invite_member.html',
                active_page='settings',
                errors=errors,
                form=request.form,
                role_info=ROLE_INFO,
                assignable_roles=ASSIGNABLE_ROLES,
            )

        new_id = max((m['id'] for m in TEAM_MEMBERS_STORE), default=0) + 1
        TEAM_MEMBERS_STORE.append({
            'id': new_id,
            'name': name,
            'email': email,
            'role': role,
            'joined': date.today().strftime('%-d %b %Y'),
        })
        return redirect(url_for('settings'))

    return render_template(
        'invite_member.html',
        active_page='settings',
        errors=[],
        form={},
        role_info=ROLE_INFO,
        assignable_roles=ASSIGNABLE_ROLES,
    )

@app.route('/support')
def support():
    return render_template('support.html', active_page='support')

@app.route('/players/import', methods=['POST'])
def import_players():
    file = request.files.get('csv_file')
    if not file or not file.filename.lower().endswith('.csv'):
        return redirect(url_for('players', import_error='Please choose a .csv file.'))

    text = file.read().decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))

    # Match column names regardless of capitals/spaces, e.g. "Name", " name ", "NAME"
    columns = {(h or '').strip().lower(): h for h in (reader.fieldnames or [])}
    if not {'name', 'gender', 'rating'} <= columns.keys():
        return redirect(url_for('players', import_error='The CSV needs Name, Gender and Rating columns.'))

    def cell(row, column):
        key = columns.get(column)
        return (row.get(key) or '').strip() if key else ''

    store = get_players_store()
    existing_names = {p['name'].strip().lower() for p in store}
    next_id = max((p['id'] for p in store), default=0) + 1
    added = skipped = 0

    for row in reader:
        name = cell(row, 'name')
        try:
            rating = int(float(cell(row, 'rating')))
        except ValueError:
            skipped += 1
            continue
        if not name or name.lower() in existing_names:
            skipped += 1
            continue

        gender_raw = cell(row, 'gender').upper()
        gender = {'M': 'Male', 'MALE': 'Male', 'F': 'Female', 'FEMALE': 'Female'}.get(
            gender_raw, gender_raw.title()
        )

        store.append({
            'id': next_id,
            'name': name,
            'rating': rating,
            'rating_dots': rating_to_dots(rating),
            'gender': gender,
            'notes': cell(row, 'notes'),
            'court_preference': cell(row, 'court preference') or cell(row, 'court_preference'),
        })
        existing_names.add(name.lower())
        next_id += 1
        added += 1

    return redirect(url_for('players', imported=added, skipped=skipped))

if __name__ == '__main__':
    app.run(debug=True)

