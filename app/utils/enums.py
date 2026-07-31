from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    HOD = "hod"
    TRAINER = "trainer"
    TRAINEE = "trainee"
