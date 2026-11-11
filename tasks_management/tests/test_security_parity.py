"""
Security parity of the tasks:

- task needs the task search right (191001) or the search-all one (191005);
  taskGroup the task group search right (190001). They answered anonymous callers.
- the rows of `task` are Task.get_queryset (admin / triage: all; else the tasks of the
  groups the user executes, once triaged), shared by the mutations.
- updateTask: the target is looked up among the tasks the user may see, and only the
  triage moves a task to another group.
- resolveTask: the task update right, an executor of the task's group, and their own
  business_status entry only (an entry under every executor id approved the task).
"""
import json

from django.core.cache import cache
from django.test import TestCase

from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import create_right_only_user, get_test_token, perm_ids
from tasks_management.apps import TasksManagementConfig as Cfg
from tasks_management.gql_mutations import ResolveTaskMutation, UpdateTaskMutation
from tasks_management.models import Task, TaskExecutor, TaskGroup
from tasks_management.services import TaskService

APPROVED = "APPROVED"


def _u(*lists):
    return list(dict.fromkeys(r for lst in lists for r in lst))


def _build(cls, prefix):
    group_rights = perm_ids(_u(Cfg.gql_task_group_search_perms, Cfg.gql_task_group_create_perms,
                               Cfg.gql_task_group_update_perms, Cfg.gql_task_group_delete_perms))
    task_rights = perm_ids(_u(Cfg.gql_task_search_perms, Cfg.gql_task_update_perms))
    cls.triage = create_right_only_user(f"{prefix}triage", _u(group_rights, task_rights))
    cls.executor = create_right_only_user(f"{prefix}exec", task_rights)
    # an executor of the same group holding the search right only (no update right)
    cls.reader = create_right_only_user(f"{prefix}read", perm_ids(Cfg.gql_task_search_perms))
    cls.outsider = create_right_only_user(f"{prefix}out", task_rights)
    cls.no_right = create_right_only_user(f"{prefix}none", [])
    username = cls.triage.username
    cls.group = TaskGroup(code=f"{prefix}G1", completion_policy=TaskGroup.TaskGroupCompletionPolicy.ALL,
                          json_ext={"task_sources": []})
    cls.group.save(username=username)
    cls.other_group = TaskGroup(code=f"{prefix}G2", completion_policy=TaskGroup.TaskGroupCompletionPolicy.ANY,
                                json_ext={"task_sources": []})
    cls.other_group.save(username=username)
    for user in (cls.executor, cls.reader):
        TaskExecutor(user=user, task_group=cls.group).save(username=username)
    TaskExecutor(user=cls.outsider, task_group=cls.other_group).save(username=username)
    cls.task = Task(source=f"{prefix}src", status=Task.Status.ACCEPTED, task_group=cls.group,
                    executor_action_event=Cfg.default_executor_event, business_status={})
    cls.task.save(username=username)
    cls.received = Task(source=f"{prefix}rcv", status=Task.Status.RECEIVED, task_group=cls.group,
                        executor_action_event=Cfg.default_executor_event, business_status={})
    cls.received.save(username=username)


class TaskQueryRightsTest(openIMISGraphQLTestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "TMQ")

    def setUp(self):
        cache.clear()

    def _q(self, query, user=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {get_test_token(user)}"} if user else {}
        return json.loads(self.query(query, headers=headers).content)

    def _sources(self, user):
        content = self._q('{ task { edges { node { source } } } }', user)
        self.assertNotIn("errors", content, content.get("errors"))
        return {e["node"]["source"] for e in content["data"]["task"]["edges"]}

    def _refused(self, content, field):
        self.assertIn("errors", content, content)
        self.assertIsNone((content.get("data") or {}).get(field), content)

    def test_task_needs_the_search_right(self):
        self._refused(self._q('{ task { edges { node { source } } } }'), "task")
        self._refused(self._q('{ task { edges { node { source } } } }', self.no_right), "task")

    def test_task_rows(self):
        self.assertIn("TMQsrc", self._sources(self.executor))
        self.assertNotIn("TMQrcv", self._sources(self.executor))  # not triaged yet
        self.assertFalse({"TMQsrc", "TMQrcv"} & self._sources(self.outsider))
        self.assertTrue({"TMQsrc", "TMQrcv"} <= self._sources(self.triage))

    def test_model_scope(self):
        ids = set(Task.get_queryset(Task.objects.all(), self.outsider).values_list("id", flat=True))
        self.assertNotIn(self.task.id, ids)
        self.assertFalse(Task.get_queryset(Task.objects.all(), None).exists())

    def test_task_group_needs_the_search_right(self):
        query = '{ taskGroup(search: "TMQ") { edges { node { code } } } }'
        self._refused(self._q(query), "taskGroup")
        self._refused(self._q(query, self.executor), "taskGroup")
        content = self._q(query, self.triage)
        self.assertNotIn("errors", content, content.get("errors"))
        self.assertIn("TMQG1", {e["node"]["code"] for e in content["data"]["taskGroup"]["edges"]})


class TaskWriteScopeTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        _build(cls, "TMW")

    def setUp(self):
        cache.clear()

    def _task(self):
        return Task.objects.get(id=self.task.id)

    def test_resolve_needs_the_update_right(self):
        errors = ResolveTaskMutation.async_mutate(
            self.reader, id=self.task.id, business_status={str(self.reader.id): APPROVED})
        self.assertTrue(errors, errors)
        self.assertEqual({}, self._task().business_status)

    def test_resolve_by_a_non_executor_refused(self):
        result = TaskService(self.outsider).resolve_task(
            {"id": self.task.id, "business_status": {str(self.outsider.id): APPROVED}})
        self.assertFalse(result["success"], result)
        self.assertEqual({}, self._task().business_status)
        errors = ResolveTaskMutation.async_mutate(
            self.outsider, id=self.task.id, business_status={str(self.outsider.id): APPROVED})
        self.assertTrue(errors, errors)
        self.assertEqual({}, self._task().business_status)

    def test_resolve_for_another_executor_refused(self):
        # approving under every executor id completed an ALL task single-handed
        result = TaskService(self.executor).resolve_task({"id": self.task.id, "business_status": {
            str(self.executor.id): APPROVED, str(self.reader.id): APPROVED}})
        self.assertFalse(result["success"], result)
        task = self._task()
        self.assertEqual({}, task.business_status)
        self.assertEqual(Task.Status.ACCEPTED, task.status)

    def test_resolve_own_status(self):
        errors = ResolveTaskMutation.async_mutate(
            self.executor, id=self.task.id, business_status={str(self.executor.id): APPROVED})
        self.assertFalse(errors, errors)
        task = self._task()
        self.assertEqual({str(self.executor.id): APPROVED}, task.business_status)
        self.assertEqual(Task.Status.ACCEPTED, task.status)  # ALL: the other executor has not

    def test_update_out_of_scope_refused(self):
        errors = UpdateTaskMutation.async_mutate(self.outsider, id=self.task.id, status="FAILED")
        self.assertTrue(errors, errors)
        self.assertEqual(Task.Status.ACCEPTED, self._task().status)

    def test_move_to_another_group_is_the_triage_s(self):
        errors = UpdateTaskMutation.async_mutate(self.executor, id=self.task.id,
                                                 task_group_id=self.other_group.id)
        self.assertTrue(errors, errors)
        self.assertEqual(self.group.id, self._task().task_group_id)
        errors = UpdateTaskMutation.async_mutate(self.triage, id=self.task.id,
                                                 task_group_id=self.other_group.id)
        self.assertFalse(errors, errors)
        self.assertEqual(self.other_group.id, self._task().task_group_id)
