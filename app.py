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

def create_schedule(signups, previous_schedules):
    if not signups:
        return None
        
    week_start = signups[0]['week_start']
    
    # Initialize schedule structure
    schedule = {
        "week_start": week_start,
        "days": [{"day": day, "chef": None, "people": []} for day in DAYS]
    }
    
    # Calculate historical cooking counts
    historical_chef_counts = {}
    for past_schedule in previous_schedules:
        for day in past_schedule["days"]:
            if day["chef"]:
                historical_chef_counts[day["chef"]] = historical_chef_counts.get(day["chef"], 0) + 1

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

    # Variable for maximum assignments per chef
    max_assignments = LpVariable("max_assignments", 0, None, LpInteger)
    
    # Variable for minimum assignments per chef
    min_assignments = LpVariable("min_assignments", 0, None, LpInteger)

    # Objective: Minimize the maximum assignments while keeping spread small
    historical_weight = 0.001
    prob += (1000 * max_assignments + 
            100 * (max_assignments - min_assignments) + 
            historical_weight * lpSum(x[day, chef] * historical_chef_counts.get(chef, 0)
                                   for day in DAYS for chef in all_chefs))

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

    # 3. Track maximum and minimum assignments per chef
    for chef in all_chefs:
        chef_assignments = lpSum(x[day, chef] for day in DAYS)
        prob += max_assignments >= chef_assignments
        prob += min_assignments <= chef_assignments

    # Solve the problem
    prob.solve()

    if LpStatus[prob.status] == 'Optimal':
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
