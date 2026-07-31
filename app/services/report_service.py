"""
Phase 7 — Full Reports Service: compliance, overdue, token-usage, annexures.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from sqlalchemy import func


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    # ─── Global Readiness ─────────────────────────────────────────────────────

    def get_global_readiness(self):
        from app.models.training import TrainingAssignment
        total = self.db.query(TrainingAssignment).count()
        if total == 0:
            return {'readiness_score': 100.0, 'total_assignments': 0, 'completed': 0, 'pending': 0, 'overdue': 0}
        completed = self.db.query(TrainingAssignment).filter(TrainingAssignment.status == 'completed').count()
        now = datetime.now(timezone.utc)
        overdue = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.status != 'completed',
            TrainingAssignment.due_date.isnot(None),
            TrainingAssignment.due_date < now,
        ).count()
        return {
            'readiness_score': round((completed / total) * 100, 2),
            'total_assignments': total,
            'completed': completed,
            'pending': total - completed,
            'overdue': overdue,
        }

    # ─── Compliance Report ────────────────────────────────────────────────────

    def get_compliance_report(self):
        from app.models.training import TrainingAssignment
        from app.models.user import User
        from app.models.user_weakness_profile import UserWeaknessProfile

        now = datetime.now(timezone.utc)
        users = self.db.query(User).filter(User.is_active == True).all()
        dept_map: dict = {}

        for user in users:
            dept = user.department or 'Unknown'
            if dept not in dept_map:
                dept_map[dept] = {
                    'department': dept,
                    'total_employees': 0,
                    'total_assignments': 0,
                    'completed': 0,
                    'overdue': 0,
                    'nq_employees': 0,
                }
            dept_map[dept]['total_employees'] += 1

            assignments = self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id == user.id
            ).all()
            dept_map[dept]['total_assignments'] += len(assignments)
            dept_map[dept]['completed'] += sum(1 for a in assignments if a.status == 'completed')
            dept_map[dept]['overdue'] += sum(
                1 for a in assignments
                if a.status != 'completed' and a.due_date and a.due_date < now
            )

            weaknesses = self.db.query(UserWeaknessProfile).filter(
                UserWeaknessProfile.user_id == user.id,
                UserWeaknessProfile.is_critical == True,
            ).count()
            if weaknesses > 0:
                dept_map[dept]['nq_employees'] += 1

        result = []
        for dept_data in dept_map.values():
            t = dept_data['total_assignments']
            c = dept_data['completed']
            dept_data['compliance_score'] = round((c / t) * 100, 2) if t > 0 else 100.0
            result.append(dept_data)

        return sorted(result, key=lambda x: x['compliance_score'])

    # ─── Overdue Report ───────────────────────────────────────────────────────

    def get_overdue_report(self, department: str | None = None):
        from app.models.training import TrainingAssignment
        from app.models.user import User
        from app.models.document import Document

        now = datetime.now(timezone.utc)
        overdue_assignments = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.status != 'completed',
            TrainingAssignment.due_date.isnot(None),
            TrainingAssignment.due_date < now,
        ).all()

        result = []
        for a in overdue_assignments:
            user = self.db.query(User).filter(User.id == a.user_id).first()
            if department and (not user or user.department != department):
                continue
            doc = self.db.query(Document).filter(Document.id == a.document_id).first() if a.document_id else None
            if a.due_date:
                due_dt = a.due_date if a.due_date.tzinfo is not None else a.due_date.replace(tzinfo=timezone.utc)
                days_overdue = (now - due_dt).days
            else:
                days_overdue = 0
            result.append({
                'assignment_id': a.id,
                'user_id': a.user_id,
                'employee_code': user.employee_code if user else None,
                'full_name': user.full_name if user else None,
                'department': user.department if user else None,
                'training_type': a.training_type,
                'document_code': doc.code if doc else None,
                'document_title': doc.title if doc else None,
                'due_date': a.due_date,
                'days_overdue': max(0, days_overdue),
                'status': a.status,
            })
        return sorted(result, key=lambda x: x['days_overdue'], reverse=True)

    # ─── NQ Employees ────────────────────────────────────────────────────────

    def get_nq_employees(self, department: str | None = None):
        from app.models.user_weakness_profile import UserWeaknessProfile
        from app.models.user import User
        from app.models.topic import Topic

        critical = self.db.query(UserWeaknessProfile).filter(
            UserWeaknessProfile.is_critical == True
        ).all()

        result = []
        seen = set()
        for w in critical:
            if w.user_id in seen:
                continue
            seen.add(w.user_id)
            user = self.db.query(User).filter(User.id == w.user_id).first()
            if department and (not user or user.department != department):
                continue
            weak_topics = self.db.query(UserWeaknessProfile).filter(
                UserWeaknessProfile.user_id == w.user_id,
                UserWeaknessProfile.is_critical == True,
            ).all()
            result.append({
                'user_id': w.user_id,
                'employee_code': user.employee_code if user else None,
                'full_name': user.full_name if user else None,
                'department': user.department if user else None,
                'critical_weak_topics': len(weak_topics),
                'avg_score': round(sum(t.score for t in weak_topics) / len(weak_topics), 2) if weak_topics else 0,
            })
        return result

    # ─── Department Compliance (existing) ────────────────────────────────────

    def get_department_compliance(self, department_name: str):
        from app.models.training import TrainingAssignment
        from app.models.user import User

        users = self.db.query(User).filter(User.department == department_name).all()
        user_ids = [u.id for u in users]
        if not user_ids:
            return {'department': department_name, 'compliance_score': 100.0, 'total_assignments': 0, 'completed': 0}

        total = self.db.query(TrainingAssignment).filter(TrainingAssignment.user_id.in_(user_ids)).count()
        if total == 0:
            return {'department': department_name, 'compliance_score': 100.0, 'total_assignments': 0, 'completed': 0}

        completed = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id.in_(user_ids),
            TrainingAssignment.status == 'completed',
        ).count()

        return {
            'department': department_name,
            'compliance_score': round((completed / total) * 100, 2),
            'total_assignments': total,
            'completed': completed,
            'employees': len(users),
        }

    # ─── Token Usage ─────────────────────────────────────────────────────────

    def get_token_usage_report(self, days: int = 30):
        from app.models.token_usage_log import TokenUsageLog

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        logs = self.db.query(TokenUsageLog).filter(TokenUsageLog.created_at >= cutoff).all()

        if not logs:
            return {
                'period_days': days,
                'total_requests': 0,
                'total_tokens': 0,
                'total_cost_usd': 0.0,
                'cache_hits': 0,
                'cache_hit_rate': 0.0,
                'by_operation': {},
            }

        total_tokens = sum(l.total_tokens for l in logs)
        total_cost = sum(l.cost_usd for l in logs)
        cache_hits = sum(1 for l in logs if l.cache_hit)

        by_operation: dict = {}
        for log in logs:
            op = log.operation
            if op not in by_operation:
                by_operation[op] = {'count': 0, 'tokens': 0, 'cost_usd': 0.0}
            by_operation[op]['count'] += 1
            by_operation[op]['tokens'] += log.total_tokens
            by_operation[op]['cost_usd'] += log.cost_usd

        return {
            'period_days': days,
            'total_requests': len(logs),
            'total_tokens': total_tokens,
            'total_cost_usd': round(total_cost, 4),
            'cache_hits': cache_hits,
            'cache_hit_rate': round(cache_hits / len(logs) * 100, 2),
            'by_operation': by_operation,
        }

    # ─── User training history (existing) ────────────────────────────────────

    def get_user_training_history(self, user_id: int):
        from app.models.training import TrainingAssignment
        from app.models.document import Document
        from app.models.user_mcq_attempt import UserMcqAttempt

        assignments = self.db.query(TrainingAssignment).filter(
            TrainingAssignment.user_id == user_id
        ).order_by(TrainingAssignment.created_at.desc()).all()

        result = []
        for a in assignments:
            doc = self.db.query(Document).filter(Document.id == a.document_id).first() if a.document_id else None
            best = (
                self.db.query(UserMcqAttempt)
                .filter(UserMcqAttempt.user_id == user_id, UserMcqAttempt.document_id == a.document_id)
                .order_by(UserMcqAttempt.score.desc())
                .first()
            ) if a.document_id else None

            result.append({
                'assignment_id': a.id,
                'training_type': a.training_type,
                'document_code': doc.code if doc else None,
                'document_title': doc.title if doc else None,
                'status': a.status,
                'due_date': a.due_date,
                'best_score': best.score if best else None,
                'passed': best.passed if best else None,
                'created_at': a.created_at,
            })
        return result
