from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime, timedelta, timezone
import psycopg2
from psycopg2.extras import Json, DictCursor
from pulp import *
import os

app = Flask(__name__)

# PostgreSQL setup
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db():
    """Get database connection"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)

# Initialize database tables
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            # Create tables if they don't exist
            cur.execute("""
                CREATE TABLE IF NOT EXISTS signups (
                    week_start TEXT,
                    person_name TEXT,
                    data JSONB,
                    PRIMARY KEY (week_start, person_name)
                );
                
                CREATE TABLE IF NOT EXISTS schedules (
                    week_start TEXT PRIMARY KEY,
                    data JSONB
                );
            """)
        conn.commit()

# Call init_db() when the application starts
init_db()

# Constants
DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

def get_current_utc():
    """Get current time in UTC."""
    return datetime.now(timezone.utc)

def get_week_start(weeks_ago):
    """
    Get the UTC datetime for Monday 00:00:00 of the target week.
    Args:
        weeks_ago: int where 0=next week, 1=this week, 2=last week
    Returns:
        datetime: The UTC datetime for Monday 00:00:00 of the target week
    """
    current_utc = get_current_utc()
    days_since_monday = current_utc.weekday()  # Monday=0, Sunday=6
    this_monday = current_utc - timedelta(days=days_since_monday)
    this_monday = this_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return this_monday + timedelta(weeks=(1 - weeks_ago))

def date_to_str(dt):
    """Convert datetime to YYYY-MM-DD string."""
    return dt.strftime("%Y-%m-%d")


@app.route('/', methods=['GET'])
@app.route('/signup', methods=['GET'])
@app.route('/signup/<name>', methods=['GET'])
@app.route('/signup/<name>/<int:weeks_ago>', methods=['GET'])
def signup(name=None, weeks_ago=1):
    if request.headers.get('Accept') == 'application/json':
        week_start = date_to_str(get_week_start(weeks_ago))
        
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT data FROM signups WHERE week_start = %s AND person_name = %s",
                    (week_start, name)
                )
                result = cur.fetchone()
                
                if result:
                    return result[0], 200
                
                # Create empty signup
                empty_signup = {
                    "week_start": week_start,
                    "person": {"name": name} if name else None,
                    "days": [
                        {"day": day, "availability": "Wants to Eat", "guests": 0}
                        for day in DAYS
                    ]
                }
                return empty_signup, 200
    return render_template('index.html')

@app.route('/schedule', methods=['GET'])
@app.route('/schedule/<int:weeks_ago>', methods=['GET'])
def schedule(weeks_ago=1):
    if request.headers.get('Accept') == 'application/json':
        week_start = date_to_str(get_week_start(weeks_ago))
        
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT data FROM schedules WHERE week_start = %s",
                    (week_start,)
                )
                result = cur.fetchone()
                
                if result:
                    return result[0], 200
                
                return {
                    "week_start": week_start,
                    "days": [
                        {"day": day, "chef": None, "people": []}
                        for day in DAYS
                    ]
                }, 200
    return render_template('schedule.html')

@app.route('/submit_signup', methods=['POST'])
def submit_signup():
    data = request.get_json()
    if not data:
        return {"error": "Invalid request body"}, 400

    person = data.get('person')
    if not person or not isinstance(person.get('name'), str) or not person['name'].strip():
        return {"error": "Name is required"}, 400

    data['person']['name'] = data['person']['name'].strip()

    weeks_ago = data.get('weeks_ago', 1)
    person_name = data['person']['name']
    
    week_start = date_to_str(get_week_start(weeks_ago))
    data['week_start'] = week_start
    
    with get_db() as conn:
        with conn.cursor() as cur:
            # Update or insert signup
            cur.execute(
                """
                INSERT INTO signups (week_start, person_name, data)
                VALUES (%s, %s, %s)
                ON CONFLICT (week_start, person_name) 
                DO UPDATE SET data = %s
                """,
                (week_start, person_name, Json(data), Json(data))
            )
            
            # Get all signups for this week
            cur.execute(
                "SELECT data FROM signups WHERE week_start = %s",
                (week_start,)
            )
            week_signups = [row[0] for row in cur.fetchall()]
            
            # Create schedule for this week
            schedule = create_schedule(week_signups, get_previous_schedules(week_start))
            
            # Update or insert schedule
            if schedule:
                cur.execute(
                    """
                    INSERT INTO schedules (week_start, data)
                    VALUES (%s, %s)
                    ON CONFLICT (week_start) 
                    DO UPDATE SET data = %s
                    """,
                    (week_start, Json(schedule), Json(schedule))
                )
        
        conn.commit()
    
    return {"message": "Signup submitted successfully!"}, 200

@app.route('/stats/<name>', methods=['GET'])
def get_stats(name):
    if request.headers.get('Accept') != 'application/json':
        return "Not found", 404
        
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get the last 4 weeks of schedules
            cur.execute(
                """
                SELECT data FROM schedules 
                WHERE week_start < %s 
                ORDER BY week_start DESC 
                LIMIT 4
                """,
                (date_to_str(get_week_start(1)),)
            )
            schedules = [row[0] for row in cur.fetchall()]
            
            # Initialize stats
            stats = {
                "dinners_eaten": 0,
                "dinners_cooked": 0,
                "people_cooked_for": 0,
                "mooch_score": 0
            }
            
            # Calculate raw counts from schedules
            for schedule in schedules:
                for day in schedule["days"]:
                    # Count dinners eaten
                    if any(person["name"] == name for person in day.get("people", [])):
                        stats["dinners_eaten"] += 1
                    
                    # Count dinners cooked and people cooked for
                    if day.get("chef") == name:
                        stats["dinners_cooked"] += 1
                        people_count = 0
                        # Count total people cooked for (including guests)
                        for person in day.get("people", []):
                            people_count += 1 + person.get("guests", 0)
                        stats["people_cooked_for"] += people_count
            
            # Calculate mooch score using shared functions
            eating_score, cooking_score, max_possible_eating_score = calculate_eating_cooking_scores(schedules, name)
            stats["mooch_score"] = calculate_mooch_score(eating_score, cooking_score, max_possible_eating_score)
            
            return stats, 200

def calculate_eating_cooking_scores(schedules, person_name):
    """
    Calculate eating and cooking scores for a person based on their history.
    
    Args:
        schedules: List of schedule data from previous weeks
        person_name: Name of the person to calculate scores for
        
    Returns:
        tuple: (eating_score, cooking_score, max_possible_eating_score)
    """
    eating_score = 0
    cooking_score = 0
    max_possible_eating_score = 0
    
    # Calculate weighted eating and cooking scores across previous weeks
    for i, past_schedule in enumerate(schedules[:4]):  # Only consider up to 4 weeks
        week_weight = 2 ** (3 - i)  # 8, 4, 2, 1 for weeks 1, 2, 3, 4 respectively
        max_possible_eating_score += week_weight * 7
        
        # Count eating instances
        week_eating_score = 0
        week_cooking_score = 0
        for day in past_schedule["days"]:
            if person_name in [eater["name"] for eater in day.get("people", [])]:
                week_eating_score += 1
            
            # Count cooking instances
            if day.get("chef") == person_name:
                # Calculate cooking score based on number of people cooked for
                eater_count = 0
                for eater in day.get("people", []):
                    # Count the person
                    eater_count += 1
                    # Add their guests
                    eater_count += eater.get("guests", 0)
                
                # This score is meant to reflect a relative measure of how much effort it is to feed a group of that size.
                day_cooking_score = 0
                if eater_count >= 7:
                    day_cooking_score = 4
                elif eater_count >= 4:
                    day_cooking_score = 3
                elif eater_count >= 2:
                    day_cooking_score = 2
                
                week_cooking_score += day_cooking_score

        cooking_score += week_cooking_score * week_weight
        eating_score += week_eating_score * week_weight
    
    return eating_score, cooking_score, max_possible_eating_score

def calculate_mooch_score(eating_score, cooking_score, max_possible_eating_score):
    """
    Calculate mooch score based on eating and cooking scores.
    
    Args:
        eating_score: Weighted score of dinners eaten
        cooking_score: Weighted score of dinners cooked
        max_possible_eating_score: Maximum possible eating score
        
    Returns:
        float: The calculated mooch score
    """
    if cooking_score == 0 and eating_score == 0:
        return 1
    elif cooking_score == 0:
        return eating_score * max_possible_eating_score
    else:
        return eating_score / cooking_score

def calculate_mooch_scores(signups, previous_schedules):
    """
    Calculate mooch scores for each person based on their eating and cooking history.
    
    Args:
        signups: List of current week's signups
        previous_schedules: List of previous weeks' schedules
        
    Returns:
        tuple: (dict of person names to mooch scores, max_possible_mooch_score)
    """
    mooch_scores = {}
    max_possible_mooch_score = 0
    
    for person_name in {signup["person"]["name"] for signup in signups}:
        eating_score, cooking_score, max_possible_eating_score = calculate_eating_cooking_scores(previous_schedules, person_name)
        mooch_scores[person_name] = calculate_mooch_score(eating_score, cooking_score, max_possible_eating_score)
        max_possible_mooch_score = max(max_possible_mooch_score, max_possible_eating_score**2)
    
    return mooch_scores, max_possible_mooch_score

def create_schedule(signups, previous_schedules):
    if not signups:
        return None
        
    week_start = signups[0]['week_start']
    
    # Initialize schedule structure
    schedule = {
        "week_start": week_start,
        "days": [{"day": day, "chef": None, "people": []} for day in DAYS]
    }
    
    # Calculate mooch scores for each person
    mooch_scores, max_possible_mooch_score = calculate_mooch_scores(signups, previous_schedules)
    
    # Collect availability and eater information
    day_to_chefs = {day: [] for day in DAYS}
    day_to_eaters = {day: [] for day in DAYS}
    all_chefs = set()
    
    for signup in signups:
        person_name = signup["person"]["name"]
        for day_data in signup["days"]:
            day_name = day_data["day"]
            if day_data["availability"] == "Available to Cook":
                day_to_chefs[day_name].append(person_name)
                all_chefs.add(person_name)
                day_to_eaters[day_name].append({
                    "name": person_name,
                    "guests": day_data["guests"]
                })
            elif day_data["availability"] == "Wants to Eat":
                day_to_eaters[day_name].append({
                    "name": person_name,
                    "guests": day_data["guests"]
                })

    # Create the optimization problem
    prob = LpProblem("Fair_Chef_Assignment", LpMinimize)

    # Decision Variables
    x = LpVariable.dicts("assignment",
                        ((day, chef) for day in DAYS for chef in all_chefs),
                        cat='Binary')
    y = LpVariable.dicts("more_than_once", all_chefs, cat='Binary')
    z = LpVariable.dicts("assigned_at_least_once", all_chefs, cat='Binary')

    # Variable for total mooch score - we want the assignment that has the highest
    # total mooch score because we want the people with the highest mooch scores to be assigned.
    total_mooch_expr = lpSum(x[day, chef] * mooch_scores.get(chef, 0)
                         for day in DAYS for chef in all_chefs)

    # Objective: minimize number of people assigned more than once, maximize those assigned at least once,
    # and maximize total mooch score as a tiebreaker
    # Scale weights relative to max_possible_mooch_score to ensure proper prioritization
    assignment_weight = max_possible_mooch_score 
    lambda_weight = 0.01 * max_possible_mooch_score  # weight for encouraging everyone to be assigned at least once
    mooch_weight = 0.001 # small weight for mooch score tiebreaker
    prob += (assignment_weight * lpSum(y[chef] for chef in all_chefs) - 
             lambda_weight * lpSum(z[chef] for chef in all_chefs) -
             mooch_weight * total_mooch_expr)  # negative because we want to maximize mooch score

    # Constraints
    # 1. Each day must have exactly one chef if there are available chefs
    for day in DAYS:
        if day_to_chefs[day]:  # If there are available chefs for this day
            prob += lpSum(x[day, chef] for chef in all_chefs) == 1

    # 2. Chefs can only be assigned on days they're available
    for day in DAYS:
        for chef in all_chefs:
            if chef not in day_to_chefs[day]:
                prob += x[day, chef] == 0

    # Track if a person is assigned more than once
    for chef in all_chefs:
        prob += lpSum(x[day, chef] for day in DAYS) <= 1 + (len(DAYS) - 1) * y[chef]

    # Track if a person is assigned at least once
    for chef in all_chefs:
        prob += lpSum(x[day, chef] for day in DAYS) >= z[chef]

    # Solve the problem
    prob.solve()

    if LpStatus[prob.status] == 'Optimal':
        schedule["schedule_objective_fn"] = {
            "value": value(prob.objective),
            "total_mooch_score": value(total_mooch_expr),
            "people_assigned_more_than_once": sum(value(y[chef]) for chef in all_chefs),
            "people_assigned_at_least_once": sum(value(z[chef]) for chef in all_chefs)
        }
        # Extract the solution
        for day_schedule in schedule["days"]:
            day = day_schedule["day"]
            # Find assigned chef for this day
            assigned_chef = None
            for chef in all_chefs:
                if abs(value(x[day, chef]) - 1.0) < 1e-7:  # Compare with small tolerance
                    assigned_chef = chef
                    break
            
            day_schedule["chef"] = assigned_chef
            day_schedule["people"] = day_to_eaters[day]
    
    return schedule

def get_previous_schedules(week_start):
    """Return schedules from weeks before the given week_start date."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data FROM schedules WHERE week_start < %s",
                (week_start,)
            )
            return [row[0] for row in cur.fetchall()]

if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_ENV') != 'production')
