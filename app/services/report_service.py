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
        from sqlalchemy import case, func
        now = datetime.now(timezone.utc)
        # Single round-trip: derive total/completed/overdue from one aggregate query.
        row = self.db.query(
            func.count(TrainingAssignment.id),
            func.coalesce(func.sum(case((TrainingAssignment.status == 'completed', 1), else_=0)), 0),
            func.coalesce(func.sum(case((
                (TrainingAssignment.status != 'completed')
                & TrainingAssignment.due_date.isnot(None)
                & (TrainingAssignment.due_date < now),
                1,
            ), else_=0)), 0),
        ).first()
        total, completed, overdue = int(row[0] or 0), int(row[1] or 0), int(row[2] or 0)
        if total == 0:
            return {'readiness_score': 100.0, 'total_assignments': 0, 'completed': 0, 'pending': 0, 'overdue': 0}
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
        from sqlalchemy import func

        now = datetime.now(timezone.utc)
        users = self.db.query(User).filter(User.is_active == True).all()
        user_ids = [u.id for u in users]
        user_dept = {u.id: (u.department or 'Unknown') for u in users}
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

        # N+1 fix: fetch ALL assignments for these users in ONE query, then
        # aggregate in memory instead of one query per user.
        assignments_by_user: dict[int, list] = {}
        if user_ids:
            for a in self.db.query(TrainingAssignment).filter(
                TrainingAssignment.user_id.in_(user_ids)
            ).all():
                assignments_by_user.setdefault(a.user_id, []).append(a)

        for uid, assignments in assignments_by_user.items():
            dept = user_dept[uid]
            dept_map[dept]['total_assignments'] += len(assignments)
            dept_map[dept]['completed'] += sum(1 for a in assignments if a.status == 'completed')
            dept_map[dept]['overdue'] += sum(
                1 for a in assignments
                if a.status != 'completed' and a.due_date and a.due_date < now
            )

        # N+1 fix: critical-weakness counts per user via ONE grouped query.
        if user_ids:
            critical_counts = dict(
                self.db.query(
                    UserWeaknessProfile.user_id,
                    func.count(UserWeaknessProfile.id),
                )
                .filter(
                    UserWeaknessProfile.user_id.in_(user_ids),
                    UserWeaknessProfile.is_critical == True,
                )
                .group_by(UserWeaknessProfile.user_id)
                .all()
            )
            for uid in user_ids:
                if critical_counts.get(uid, 0) > 0:
                    dept_map[user_dept[uid]]['nq_employees'] += 1

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

        # N+1 fix: batch-fetch referenced users + documents in two queries.
        user_ids = {a.user_id for a in overdue_assignments if a.user_id}
        doc_ids = {a.document_id for a in overdue_assignments if a.document_id}
        users = {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}

        result = []
        for a in overdue_assignments:
            user = users.get(a.user_id)
            if department and (not user or user.department != department):
                continue
            doc = docs.get(a.document_id)
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

        # N+1 fix: group all critical profiles by user from the single fetch,
        # then batch-load users with one query.
        critical_by_user: dict[int, list] = {}
        for w in critical:
            critical_by_user.setdefault(w.user_id, []).append(w)

        user_ids = list(critical_by_user.keys())
        users = {u.id: u for u in self.db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

        result = []
        for user_id, weak_topics in critical_by_user.items():
            user = users.get(user_id)
            if department and (not user or user.department != department):
                continue
            result.append({
                'user_id': user_id,
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

        # N+1 fix: batch-fetch documents, then derive best attempt per document
        # from a single attempts query instead of one query per assignment.
        doc_ids = {a.document_id for a in assignments if a.document_id}
        docs = {d.id: d for d in self.db.query(Document).filter(Document.id.in_(doc_ids)).all()} if doc_ids else {}
        best_by_doc: dict[int, UserMcqAttempt] = {}
        if doc_ids:
            for att in self.db.query(UserMcqAttempt).filter(
                UserMcqAttempt.user_id == user_id,
                UserMcqAttempt.document_id.in_(doc_ids),
            ).all():
                current = best_by_doc.get(att.document_id)
                if current is None or (att.score or 0) > (current.score or 0):
                    best_by_doc[att.document_id] = att

        result = []
        for a in assignments:
            doc = docs.get(a.document_id)
            best = best_by_doc.get(a.document_id) if a.document_id else None

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
