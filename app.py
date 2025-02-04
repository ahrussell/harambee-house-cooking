from flask import Flask, render_template, request, redirect, url_for
from datetime import datetime

app = Flask(__name__)

# In-memory storage
signups = {} # week_key -> dict of name -> signup
schedules = {} # week_key -> schedule

@app.route('/signup')
@app.route('/signup/<name>')
@app.route('/signup/<name>/<int:weeks_ago>')
def signup(name=None, weeks_ago=0):
    if request.headers.get('Accept') == 'application/json':
        signup_data = None
        if weeks_ago in signups and name in signups[weeks_ago]:
            signup_data = signups[weeks_ago][name]
        return signup_data or {"days": []}, 200
    return render_template('index.html')

@app.route('/schedule')
@app.route('/schedule/<int:weeks_ago>')
def schedule(weeks_ago=0):
    if request.headers.get('Accept') == 'application/json':
        return schedules.get(weeks_ago, {"days": []}), 200
    return render_template('schedule.html')

@app.route('/submit_signup', methods=['POST'])
def submit_signup():
    # Get form data
    data = request.get_json()
    weeks_ago = data.get('weeks_ago', 0)
    person_name = data['person']['name']
    
    # Initialize week if needed
    if weeks_ago not in signups:
        signups[weeks_ago] = {}
        
    # Add/update signup for this person
    signups[weeks_ago][person_name] = data
    
    # Recreate schedule for this week using all signups
    schedule = create_schedule(list(signups[weeks_ago].values()), get_previous_schedules(weeks_ago))
    schedules[weeks_ago] = schedule
    
    return {"message": "Signup submitted successfully!"}, 200

def create_schedule(signups, previous_schedules):
    """
    Given signups for one week and previous schedules, create a schedule for the week.
    Args:
        signups: List of signups for one specific week
        previous_schedules: List of schedules from previous weeks
    """
    # Initialize schedule
    schedule = {"days": []}
    
    # Track cooking assignments for this week
    chef_assignments = {}  # chef name -> number of times assigned this week
    
    # Calculate historical cooking counts from previous schedules
    historical_chef_counts = {}
    for past_schedule in previous_schedules:
        for day in past_schedule["days"]:
            if day["chef"]:
                historical_chef_counts[day["chef"]] = historical_chef_counts.get(day["chef"], 0) + 1
    
    # Process each day
    for day_index in range(7):
        day_schedule = {
            "date": None,
            "chef": None,
            "people": []
        }
        
        # Get all signups for this day
        available_chefs = []
        eaters = []
        
        for signup in signups:
            person_name = signup["person"]["name"]
            day_data = signup["days"][day_index]
            
            # Set the date if not set yet
            if not day_schedule["date"]:
                day_schedule["date"] = day_data["date"]
                
            # Track who can cook
            if day_data["availability"] == "Available to Cook":
                available_chefs.append({
                    "name": person_name,
                    "week_assignments": chef_assignments.get(person_name, 0),
                    "historical_count": historical_chef_counts.get(person_name, 0)
                })
                # Chef also eats
                eaters.append({
                    "name": person_name,
                    "guests": day_data["guests"]
                })
                
            # Track who wants to eat
            elif day_data["availability"] == "Wants to Eat":
                eaters.append({
                    "name": person_name,
                    "guests": day_data["guests"]
                })
        
        # Assign chef if we have available chefs
        if available_chefs:
            # First sort by assignments this week, then by historical count
            available_chefs.sort(key=lambda x: (x["week_assignments"], x["historical_count"]))
            chosen_chef = available_chefs[0]["name"]
            
            # Update assignments count for this week
            chef_assignments[chosen_chef] = chef_assignments.get(chosen_chef, 0) + 1
            
            day_schedule["chef"] = chosen_chef
        
        day_schedule["people"] = eaters
        schedule["days"].append(day_schedule)
    
    return schedule

def get_previous_schedules(weeks_ago=0):
    # Return schedules from previous weeks
    return [schedules[week] for week in schedules if week > weeks_ago]

def get_signups_for_week(weeks_ago=0):
    # Mock data for testing
    mock_signups = {
        "Andrew": {
            "person": {
                "name": "Andrew"
            },
            "days": [
                {
                    "date": "2024-01-22",
                    "availability": "Available to Cook", 
                    "guests": 2
                },
                {
                    "date": "2024-01-23",
                    "availability": "Wants to Eat",
                    "guests": 0
                },
                {
                    "date": "2024-01-24", 
                    "availability": "Unavailable",
                    "guests": 0
                },
                {
                    "date": "2024-01-25",
                    "availability": "Available to Cook",
                    "guests": 3
                },
                {
                    "date": "2024-01-26",
                    "availability": "Wants to Eat",
                    "guests": 1
                },
                {
                    "date": "2024-01-27",
                    "availability": "Unavailable",
                    "guests": 0
                },
                {
                    "date": "2024-01-28",
                    "availability": "Wants to Eat",
                    "guests": 0
                }
            ]
        },
        "Maddy": {
            "person": {
                "name": "Maddy"  
            },
            "days": [
                {
                    "date": "2024-01-22",
                    "availability": "Wants to Eat",
                    "guests": 0
                },
                {
                    "date": "2024-01-23", 
                    "availability": "Available to Cook",
                    "guests": 4
                },
                {
                    "date": "2024-01-24",
                    "availability": "Wants to Eat",
                    "guests": 1
                },
                {
                    "date": "2024-01-25",
                    "availability": "Unavailable",
                    "guests": 0
                },
                {
                    "date": "2024-01-26",
                    "availability": "Available to Cook",
                    "guests": 2
                },
                {
                    "date": "2024-01-27",
                    "availability": "Wants to Eat",
                    "guests": 0
                },
                {
                    "date": "2024-01-28",
                    "availability": "Unavailable",
                    "guests": 0
                }
            ]
        }
    }
    
    return signups.get(weeks_ago, mock_signups)

if __name__ == '__main__':
    app.run(debug=True)
