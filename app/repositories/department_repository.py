from sqlalchemy.orm import Session
from app.models.department import Department


class DepartmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_all(self):
        return self.db.query(Department).filter(Department.is_active == True).order_by(Department.name).all()

    def get(self, dept_id: int):
        return self.db.query(Department).filter(Department.id == dept_id).first()

    def get_by_name(self, name: str):
        return self.db.query(Department).filter(Department.name == name).first()

    def create(self, dept: Department):
        self.db.add(dept)
        self.db.commit()
        self.db.refresh(dept)
        return dept

    def update(self, dept: Department, **kwargs):
        for k, v in kwargs.items():
            setattr(dept, k, v)
        self.db.commit()
        self.db.refresh(dept)
        return dept

    def delete(self, dept_id: int) -> bool:
        dept = self.get(dept_id)
        if not dept:
            return False
        dept.is_active = False  # soft delete
        self.db.commit()
        return True
