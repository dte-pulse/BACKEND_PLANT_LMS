import os
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import SessionLocal, Base, engine
from app.models.user import User, UserRole
from app.models.subject import Subject
from app.models.topic import Topic
from app.core.security import get_password_hash

def seed():
    # Make sure tables exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Check if users already exist
        admin = db.query(User).filter(User.employee_code == 'admin').first()
        if not admin:
            print("Seeding users...")
            admin = User(
                employee_code='admin',
                full_name='System Admin',
                email='admin@plantlms.com',
                hashed_password=get_password_hash('adminpassword'),
                department='IT',
                role=UserRole.admin,
                is_active=True
            )
            trainer = User(
                employee_code='trainer',
                full_name='John Doe (Trainer)',
                email='trainer@plantlms.com',
                hashed_password=get_password_hash('trainerpassword'),
                department='Production',
                role=UserRole.trainer,
                is_active=True
            )
            trainee = User(
                employee_code='trainee',
                full_name='Jane Smith (Trainee)',
                email='trainee@plantlms.com',
                hashed_password=get_password_hash('traineepassword'),
                department='Production',
                role=UserRole.trainee,
                is_active=True
            )
            hod = User(
                employee_code='hod',
                full_name='Robert Johnson (HOD)',
                email='hod@plantlms.com',
                hashed_password=get_password_hash('hodpassword'),
                department='Production',
                role=UserRole.hod,
                is_active=True
            )
            db.add_all([admin, trainer, trainee, hod])
            db.commit()
            print("Users seeded successfully!")
        else:
            print("Users already exist, skipping user seeding.")

        # Check if subjects exist
        gmp = db.query(Subject).filter(Subject.name == 'GMP').first()
        if not gmp:
            print("Seeding subjects and topics...")
            gmp = Subject(name='GMP', department='QA')
            sop = Subject(name='SOP', department='Production')
            db.add_all([gmp, sop])
            db.commit()

            # Seed topics
            t1 = Topic(subject_id=gmp.id, title='Hygiene Standard', sequence_order=1)
            t2 = Topic(subject_id=gmp.id, title='Line Clearance', sequence_order=2)
            t3 = Topic(subject_id=sop.id, title='Granulation Machine Cleaning', sequence_order=1)
            db.add_all([t1, t2, t3])
            db.commit()
            print("Subjects and topics seeded successfully!")
        else:
            print("Subjects already exist, skipping subject/topic seeding.")

    except Exception as e:
        print(f"Error during seeding: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == '__main__':
    seed()
