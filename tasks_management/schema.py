import graphene
import json
import graphene_django_optimizer as gql_optimizer

from django.contrib.auth.models import AnonymousUser
from django.db.models import Q

from core.schema import OrderedDjangoFilterConnectionField
from core.utils import append_validity_filter
from core.gql import legacy_row_filters
from core.models import Validity
from tasks_management.gql_mutations import CreateTaskGroupMutation, UpdateTaskGroupMutation, DeleteTaskGroupMutation, \
    UpdateTaskMutation, ResolveTaskMutation
from tasks_management.gql_queries import TaskGroupGQLType, TaskExecutorGQLType, TaskGQLType
from tasks_management.models import TaskGroup, TaskExecutor, Task
from tasks_management.apps import TasksManagementConfig


class Query(graphene.ObjectType):
    module_name = "tasks_management"

    task_group = OrderedDjangoFilterConnectionField(
        TaskGroupGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        dateValidFrom__Gte=graphene.DateTime(),
        dateValidTo__Lte=graphene.DateTime(),
        client_mutation_id=graphene.String(),
        search=graphene.String(),
    )
    task_executor = OrderedDjangoFilterConnectionField(
        TaskExecutorGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        dateValidFrom__Gte=graphene.DateTime(),
        dateValidTo__Lte=graphene.DateTime(),
        client_mutation_id=graphene.String(),
        taskGroupIdString=graphene.String(),
    )

    task = OrderedDjangoFilterConnectionField(
        TaskGQLType,
        orderBy=graphene.List(of_type=graphene.String),
        applyDefaultValidityFilter=graphene.Boolean(),
        client_mutation_id=graphene.String(),
        groupId=graphene.String(),
        customFilters=graphene.List(of_type=graphene.String),
        taskGroupId=graphene.String(),
        entityIds=graphene.List(graphene.UUID),
        entityString__Icontains=graphene.String(),
    )

    def resolve_task(self, info, **kwargs):
        # the search right (191001) or the search-all one (191005); the rows are narrowed
        # to the tasks the user may see by Task.get_queryset (TaskGQLType)
        Query._check_task_search_permissions(info.context.user)
        # validity is applied by Task.get_queryset: live tasks unless isDeleted asks
        # otherwise (append_validity_filter filtered business-validity columns a task
        # does not have)
        legacy_row_filters(info, kwargs, validity=Validity.NOW)
        filters = []

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        taskGroupId = kwargs.get("taskGroupId")
        if taskGroupId:
            filters.append(Q(task_group__id=taskGroupId))

        entityIds = kwargs.get("entityIds")
        if entityIds:
            filters.append(Q(entity_id__in=entityIds))

        # scoped here already (validity left to TaskGQLType) so that the entity string
        # search below reads the user's tasks only
        query = Task.get_queryset(Task.objects.filter(*filters), info.context.user, validity=Validity.ALL)

        entity_string = kwargs.get("entityString__Icontains")
        if entity_string:
            task_ids = [task.id for task in query if entity_string.lower() in str(task.entity).lower()]
            query = query.filter(id__in=task_ids)

        return gql_optimizer.query(query, info)

    def resolve_task_group(self, info, **kwargs):
        # the task groups and their executors are the triage's: the search right is
        # required (anonymous callers could list them and use `search` as a name oracle)
        Query._check_permissions(info.context.user, TasksManagementConfig.gql_task_group_search_perms)
        # live groups unless isDeleted asks otherwise (TaskGroup.get_queryset, OP-2029)
        legacy_row_filters(info, kwargs, validity=Validity.NOW)
        filters = []

        client_mutation_id = kwargs.get("client_mutation_id", None)
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        search = kwargs.get("search", None)
        if search is not None:
            filters.append(
                Q(code__icontains=search)
                | Q(taskexecutor__user__i_user__last_name__icontains=search)
                | Q(taskexecutor__user__i_user__other_names__icontains=search)
            )

        query = TaskGroup.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    def resolve_task_executor(self, info, **kwargs):
        filters = append_validity_filter(**kwargs)

        client_mutation_id = kwargs.get("client_mutation_id")
        if client_mutation_id:
            filters.append(Q(mutations__mutation__client_mutation_id=client_mutation_id))

        task_group_id_string = kwargs.get("taskGroupIdString")
        if task_group_id_string:
            filters.append(Q(taskgroup__user__id_icontains=task_group_id_string))

        Query._check_permissions(info.context.user, TasksManagementConfig.gql_task_group_search_perms)
        query = TaskExecutor.objects.filter(*filters)
        return gql_optimizer.query(query, info)

    @staticmethod
    def _check_task_search_permissions(user):
        if type(user) is AnonymousUser or not user.id or not (
                user.has_perms(TasksManagementConfig.gql_task_search_perms)
                or user.has_perms(TasksManagementConfig.gql_task_search_all_perms)):
            raise PermissionError("Unauthorized")

    @staticmethod
    def _check_permissions(user, perms):
        if type(user) is AnonymousUser or not user.id or not user.has_perms(perms):
            raise PermissionError("Unauthorized")


class Mutation(graphene.ObjectType):
    create_task_group = CreateTaskGroupMutation.Field()
    update_task_group = UpdateTaskGroupMutation.Field()
    delete_task_group = DeleteTaskGroupMutation.Field()

    update_task = UpdateTaskMutation.Field()
    resolve_task = ResolveTaskMutation.Field()
