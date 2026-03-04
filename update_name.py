#!/usr/bin/env python3
import psycopg2
from psycopg2.extras import Json, DictCursor
import os
import sys

# Get DATABASE_URL from environment or command line
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

OLD_NAME = 'Youssef'
NEW_NAME = 'Hanna'

print(f"Connecting to database...")
conn = psycopg2.connect(DATABASE_URL, cursor_factory=DictCursor)
cur = conn.cursor()

try:
    # 1. Update signups table - update person_name column
    print(f"\nUpdating signups table person_name column...")
    cur.execute(
        "UPDATE signups SET person_name = %s WHERE person_name = %s",
        (NEW_NAME, OLD_NAME)
    )
    print(f"  Updated {cur.rowcount} rows")
    
    # 2. Update signups table - update person.name in JSONB data
    print(f"\nUpdating signups table JSONB data (person.name)...")
    cur.execute(
        """
        UPDATE signups 
        SET data = jsonb_set(data, '{person,name}', %s)
        WHERE data->'person'->>'name' = %s
        """,
        (Json(NEW_NAME), OLD_NAME)
    )
    print(f"  Updated {cur.rowcount} rows")
    
    # 3. Update schedules table - update chef field in JSONB data
    print(f"\nUpdating schedules table JSONB data (chef fields)...")
    cur.execute(
        """
        SELECT week_start, data FROM schedules
        WHERE data::text LIKE %s
        """,
        (f'%{OLD_NAME}%',)
    )
    schedules_to_update = cur.fetchall()
    print(f"  Found {len(schedules_to_update)} schedules to check")
    
    updated_schedules = 0
    for row in schedules_to_update:
        week_start = row['week_start']
        schedule_data = row['data']
        
        modified = False
        # Update chef names in each day
        for day in schedule_data.get('days', []):
            if day.get('chef') == OLD_NAME:
                day['chef'] = NEW_NAME
                modified = True
            
            # Update people names in each day
            for person in day.get('people', []):
                if person.get('name') == OLD_NAME:
                    person['name'] = NEW_NAME
                    modified = True
        
        if modified:
            cur.execute(
                "UPDATE schedules SET data = %s WHERE week_start = %s",
                (Json(schedule_data), week_start)
            )
            updated_schedules += 1
    
    print(f"  Updated {updated_schedules} schedule rows")
    
    # Commit the changes
    conn.commit()
    print(f"\n✓ Successfully updated all instances of '{OLD_NAME}' to '{NEW_NAME}'")
    
    # Verify the changes
    print(f"\nVerifying changes...")
    cur.execute("SELECT COUNT(*) FROM signups WHERE person_name = %s", (OLD_NAME,))
    old_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM signups WHERE person_name = %s", (NEW_NAME,))
    new_count = cur.fetchone()[0]
    print(f"  Signups with '{OLD_NAME}': {old_count}")
    print(f"  Signups with '{NEW_NAME}': {new_count}")
    
except Exception as e:
    conn.rollback()
    print(f"\n✗ Error: {e}")
    sys.exit(1)
finally:
    cur.close()
    conn.close()
