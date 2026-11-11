from django.db import models

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.utils.translation import gettext_lazy as _

from core.models import HistoryModel, User, UUIDModel, ObjectMutation, MutationLog


def is_task_triage(user):
    """The triage holds every task group right: it sees and dispatches every task."""
    from tasks_management.apps import TasksManagementConfig
    return user.has_perms(TasksManagementConfig.gql_task_group_create_perms
                          + TasksManagementConfig.gql_task_group_search_perms
                          + TasksManagementConfig.gql_task_group_update_perms
                          + TasksManagementConfig.gql_task_group_delete_perms)


def executor_task_groups(user):
    """The live task groups `user` is a live executor of."""
    return TaskGroup.objects.filter(
        is_deleted=False, taskexecutor__user=user, taskexecutor__is_deleted=False).values('id')


class TaskGroup(HistoryModel):
    class TaskGroupCompletionPolicy(models.TextChoices):
        ALL = 'ALL', _('ALL')
        ANY = 'ANY', _('ANY')
        N = 'N', _('N')

    code = models.CharField(max_length=255, null=False, blank=False)
    completion_policy = models.CharField(
        max_length=50, choices=TaskGroupCompletionPolicy.choices, null=False, blank=False
    )


class TaskExecutor(HistoryModel):
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=False)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING, null=False)


class Task(HistoryModel):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', _('Received')
        ACCEPTED = 'ACCEPTED', _('Accepted')
        COMPLETED = 'COMPLETED', _('Completed')
        FAILED = 'FAILED', _('Failed')

    source = models.CharField(max_length=255,  blank=True, null=True)
    entity_type = models.ForeignKey(ContentType, models.DO_NOTHING, blank=True, null=True, unique=False)
    entity_id = models.CharField(max_length=255,  blank=True, null=True)
    entity = GenericForeignKey('entity_type', 'entity_id')
    status = models.CharField(max_length=255, choices=Status.choices, default=Status.RECEIVED)
    executor_action_event = models.CharField(max_length=255, blank=True, null=True)
    business_status = models.JSONField( blank=True,default=dict)
    business_event = models.CharField(max_length=255, blank=True, null=True)
    task_group = models.ForeignKey(TaskGroup, on_delete=models.DO_NOTHING,  blank=True, null=True)
    data = models.JSONField(blank=True, default=dict)
    business_data_serializer = models.CharField(max_length=255, blank=True, null=True)

    @classmethod
    def get_queryset(cls, queryset, user=None, *, validity=None, audits=None):
        """
        The tasks `user` may see: every task for an IMIS admin or the triage, otherwise
        the tasks of the groups they execute, once triaged (not RECEIVED). Live rows by
        default (OP-2029: validity / audits when asked). Anonymous: none.
        """
        queryset = cls.apply_row_filters(queryset, validity, audits, default=lambda qs: qs.filter(is_deleted=False))
        user = cls.scoping_user(user)
        if user is None or getattr(user, "is_anonymous", True) or not getattr(user, "id", None):
            return queryset.none()
        if getattr(user, "is_imis_admin", False) or is_task_triage(user):
            return queryset
        return queryset.filter(task_group__in=executor_task_groups(user)).exclude(status=cls.Status.RECEIVED)

    @classmethod
    def executor_queryset(cls, user):
        """The live, triaged tasks `user` resolves as an executor of their group (the
        admin and the triage included: resolving is an executor's act)."""
        if user is None or getattr(user, "is_anonymous", True) or not getattr(user, "id", None):
            return cls.objects.none()
        return cls.objects.filter(is_deleted=False, task_group__in=executor_task_groups(user)) \
            .exclude(status=cls.Status.RECEIVED)


class TaskMutation(UUIDModel, ObjectMutation):
    task = models.ForeignKey(Task, models.DO_NOTHING, related_name='mutations')
    mutation = models.ForeignKey(MutationLog, models.DO_NOTHING, related_name='task')
