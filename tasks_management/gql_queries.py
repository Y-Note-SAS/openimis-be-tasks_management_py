import importlib

import graphene
import copy

from django.db.models import Q
from graphene_django import DjangoObjectType

from core import ExtendedConnection, prefix_filterset
from core.gql_queries import UserGQLType
from tasks_management.apps import TasksManagementConfig
from core.gql.scoped_object_type import ScopedQuerysetMixin
from tasks_management.models import TaskGroup, TaskExecutor, Task, is_task_triage

DICT_STRING = "{}"


class TaskGQLType(ScopedQuerysetMixin, DjangoObjectType):
    uuid = graphene.String(source='uuid')
    business_data = graphene.JSONString()
    entity_string = graphene.String()

    class Meta:
        model = Task
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "entity_type": ["exact"],
            "entity_id": ["exact"],

            "source": ["exact", "iexact", "istartswith", "icontains"],
            "status": ["exact", "iexact", "istartswith", "icontains"],
            "executor_action_event": ["exact", "iexact", "istartswith", "icontains"],
            "business_event": ["exact", "iexact", "istartswith", "icontains"],

            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_business_data(self, info):
        data = self.data
        serializer_path = self.business_data_serializer
        serialized_data = copy.deepcopy(data)
        if serializer_path:
            module_path, class_name, method_name = serializer_path.rsplit('.', 2)

            try:
                service_module = importlib.import_module(module_path)

                if hasattr(service_module, class_name):
                    service_class = getattr(service_module, class_name)
                    instance = service_class(info.context.user)

                    serializer_method = getattr(instance, method_name, None)

                    if callable(serializer_method):
                        serialized_data = serializer_method(serialized_data)

            except ImportError:
                return f"Error: Module '{module_path}' not found."
            except AttributeError:
                return f"Error: Attribute not found in the module or class."
            except Exception as e:
                return f"Error: {str(e)}"

        return serialized_data

    def resolve_entity_string(self, info):
        return self.entity.__str__()

    # the rows: Task.get_queryset (ScopedQuerysetMixin), shared with the mutations


class TaskGroupGQLType(ScopedQuerysetMixin, DjangoObjectType):
    uuid = graphene.String(source='uuid')
    user = graphene.List(UserGQLType)

    class Meta:
        model = TaskGroup
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "code": ["exact", "iexact", "startswith", "istartswith", "contains", "icontains"],
            "completion_policy": ["exact", "iexact"],

            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection

    def resolve_user(self, info):
        task_group_id = self.id
        return TaskExecutor.objects.filter(task_group_id=task_group_id)


class TaskExecutorGQLType(DjangoObjectType):
    uuid = graphene.String(source='uuid')

    class Meta:
        model = TaskExecutor
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            **prefix_filterset("user__", UserGQLType._meta.filter_fields),
            **prefix_filterset("task_group__", TaskGroupGQLType._meta.filter_fields),
            "date_created": ["exact", "lt", "lte", "gt", "gte"],
            "date_updated": ["exact", "lt", "lte", "gt", "gte"],
            "is_deleted": ["exact"],
            "version": ["exact"],
        }
        connection_class = ExtendedConnection
