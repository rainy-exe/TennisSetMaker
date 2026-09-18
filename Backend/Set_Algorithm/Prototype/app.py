from flask import Flask, render_template, request, redirect, url_for
from helpers import load_players_from_csv

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

if __name__ == '__main__':
    app.run(debug=True)

