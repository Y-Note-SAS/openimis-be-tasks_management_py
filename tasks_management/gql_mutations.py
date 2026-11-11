import graphene as graphene
from django.db import transaction
from django.contrib.auth.models import AnonymousUser
from pydantic.error_wrappers import ValidationError
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError

from core.gql.gql_mutations.base_mutation import BaseHistoryModelCreateMutationMixin, BaseMutation, \
    BaseHistoryModelUpdateMutationMixin, BaseHistoryModelDeleteMutationMixin
from core.schema import OpenIMISMutation
from tasks_management.apps import TasksManagementConfig
from tasks_management.models import TaskGroup, Task, TaskMutation, is_task_triage
from tasks_management.services import TaskGroupService, TaskService


class CreateTaskGroupInput(OpenIMISMutation.Input):
    class TaskGroupCompletionPolicyEnum(graphene.Enum):
        ALL = TaskGroup.TaskGroupCompletionPolicy.ALL
        ANY = TaskGroup.TaskGroupCompletionPolicy.ANY
        N = TaskGroup.TaskGroupCompletionPolicy.N

    code = graphene.String(required=True, max_length=255)
    completion_policy = graphene.Field(TaskGroupCompletionPolicyEnum, required=True)
    user_ids = graphene.List(graphene.UUID)
    task_sources = graphene.List(graphene.String)

    def resolve_completion_policy(self, info):
        return self.completion_policy


class UpdateTaskInput(OpenIMISMutation.Input):
    class TaskStatusEnum(graphene.Enum):
        COMPLETED = Task.Status.COMPLETED
        FAILED = Task.Status.FAILED
        ACCEPTED = Task.Status.ACCEPTED

    id = graphene.UUID(required=True)
    status = graphene.Field(TaskStatusEnum, required=False)
    task_group_id = graphene.UUID(required=False)


class UpdateTaskGroupInput(CreateTaskGroupInput):
    id = graphene.UUID(required=True)


class ResolveTaskGroupInput(OpenIMISMutation.Input):
    id = graphene.UUID(required=True)
    business_status = graphene.JSONString(required=True)
    additional_data = graphene.JSONString(required=False)


class CreateTaskGroupMutation(BaseHistoryModelCreateMutationMixin, BaseMutation):
    _mutation_class = "CreateTaskGroupMutation"
    _mutation_module = "tasks_management"
    _model = TaskGroup

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                TasksManagementConfig.gql_task_group_create_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TaskGroupService(user)
        response = service.create(data)
        if not response['success']:
            return response
        return None

    class Input(CreateTaskGroupInput):
        pass


class UpdateTaskGroupMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateTaskGroupMutation"
    _mutation_module = "tasks_management"
    _model = TaskGroup

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                TasksManagementConfig.gql_task_group_update_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TaskGroupService(user)
        response = service.update(data)
        if not response['success']:
            return response
        return None

    class Input(UpdateTaskGroupInput):
        pass


class DeleteTaskGroupMutation(BaseHistoryModelDeleteMutationMixin, BaseMutation):
    _mutation_class = "DeleteTaskGroupMutation"
    _mutation_module = "tasks_management"
    _model = TaskGroup

    @classmethod
    def _validate_mutation(cls, user, **data):
        if type(user) is AnonymousUser or not user.has_perms(
                TasksManagementConfig.gql_task_group_delete_perms):
            raise ValidationError("mutation.authentication_required")

    @classmethod
    def _mutate(cls, user, **data):
        if "client_mutation_id" in data:
            data.pop('client_mutation_id')
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TaskGroupService(user)
        ids = data.get('ids')
        if ids:
            with transaction.atomic():
                for id in ids:
                    service.delete({'id': id})

    class Input(OpenIMISMutation.Input):
        ids = graphene.List(graphene.UUID)


class UpdateTaskMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "UpdateTaskMutation"
    _mutation_module = "tasks_management"
    _model = Task

    @classmethod
    def _validate_mutation(cls, user, **data):
        super()._validate_mutation(user, **data)
        if not user.has_perms(
                TasksManagementConfig.gql_task_update_perms):
            raise ValidationError("mutation.authentication_required")
        # the task must be one the user may see (Task.get_queryset), not any task by id
        task = Task.get_queryset(Task.objects.filter(id=data['id']), user).first()
        if task is None:
            cls._object_not_exist_exception(obj_uuid=data['id'])
        task_group_id = data.get('task_group_id')
        if task_group_id is not None and str(task_group_id) != str(task.task_group_id):
            # dispatching a task to another group is the triage's act, to a live group
            if not (getattr(user, "is_imis_admin", False) or is_task_triage(user)):
                raise PermissionDenied("unauthorized")
            if not TaskGroup.objects.filter(id=task_group_id, is_deleted=False).exists():
                raise DjangoValidationError("tasks_management.validation.task_group_not_found")

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TaskService(user)
        res = service.update(data)
        task = Task.objects.filter(id=data['id']).first()

        if client_mutation_id:
            TaskMutation.object_mutated(
                user, client_mutation_id=client_mutation_id, task=task
            )
        if not res['success']:
            return res
        return None

    class Input(UpdateTaskInput):
        pass


class ResolveTaskMutation(BaseHistoryModelUpdateMutationMixin, BaseMutation):
    _mutation_class = "ResolveTaskMutation"
    _mutation_module = "tasks_management"
    _model = Task

    @classmethod
    def _validate_mutation(cls, user, **data):
        # a login was enough: the task update right is required, and the service checks
        # that the user executes the task's group and writes their own status only
        super()._validate_mutation(user, **data)
        if not user.has_perms(TasksManagementConfig.gql_task_update_perms):
            raise PermissionDenied("unauthorized")

    @classmethod
    def _mutate(cls, user, **data):
        client_mutation_id = data.pop('client_mutation_id', None)
        if "client_mutation_label" in data:
            data.pop('client_mutation_label')

        service = TaskService(user)
        res = service.resolve_task(data)
        task = Task.objects.filter(id=data['id']).first()

        if client_mutation_id:
            TaskMutation.object_mutated(
                user, client_mutation_id=client_mutation_id, task=task
            )
        if not res['success']:
            return res
        return None

    class Input(ResolveTaskGroupInput):
        pass
