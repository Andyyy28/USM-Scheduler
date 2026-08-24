from rest_framework.permissions import BasePermission

from scheduler.models import UserRole


class IsCentralScheduler(BasePermission):
    message = "Central scheduler access is required."

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.is_active
            and (
                user.is_superuser
                or user.role in {UserRole.SYSTEM_ADMIN, UserRole.CENTRAL_SCHEDULER}
            )
        )


class IsSchedulerUser(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_active)
