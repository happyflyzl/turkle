# -*- coding: utf-8 -*-
try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO
        StringIO = BytesIO
import datetime
import os.path

from django.contrib.auth.models import AnonymousUser, Group, User
from django.core.exceptions import ValidationError
import django.test
from django.utils import timezone
from guardian.shortcuts import assign_perm

from turkle.models import Task, TaskAssignment, Batch, Project

# hack to add unicode() to python3 for backward compatibility
try:
    unicode('')
except NameError:
    unicode = str


class TestTaskAssignment(django.test.TestCase):
    def test_task_marked_as_completed(self):
        # When assignment_per_task==1, completing 1 Assignment marks Task as complete
        project = Project(name='test', html_template='<p>${number} - ${letter}</p>')
        project.save()
        batch = Batch(project=project)
        batch.save()

        task = Task(
            batch=batch,
            input_csv_fields={'number': '1', 'letter': 'a'}
        )
        task.save()

        self.assertEqual(batch.assignments_per_task, 1)
        self.assertFalse(task.completed)

        TaskAssignment(
            assigned_to=None,
            completed=True,
            task=task
        ).save()

        task.refresh_from_db()
        self.assertTrue(task.completed)

    def test_task_marked_as_completed_two_way_redundancy(self):
        # When assignment_per_task==2, completing 2 Assignments marks Task as complete
        project = Project(name='test', html_template='<p>${number} - ${letter}</p>')
        project.save()
        batch = Batch(project=project)
        batch.assignments_per_task = 2
        batch.save()

        task = Task(
            batch=batch,
            input_csv_fields={'number': '1', 'letter': 'a'}
        )
        task.save()

        self.assertFalse(task.completed)

        TaskAssignment(
            assigned_to=None,
            completed=True,
            task=task
        ).save()
        task.refresh_from_db()
        self.assertFalse(task.completed)

        TaskAssignment(
            assigned_to=None,
            completed=True,
            task=task
        ).save()
        task.refresh_from_db()
        self.assertTrue(task.completed)

    def test_expire_all_abandoned(self):
        t = timezone.now()
        dt = datetime.timedelta(hours=2)
        past = t - dt

        project = Project(login_required=False)
        project.save()
        batch = Batch(
            allotted_assignment_time=1,
            project=project
        )
        batch.save()
        task = Task(batch=batch)
        task.save()
        ha = TaskAssignment(
            completed=False,
            expires_at=past,
            task=task,
        )
        # Bypass TaskAssignment's save(), which updates expires_at
        super(TaskAssignment, ha).save()
        self.assertEqual(TaskAssignment.objects.count(), 1)
        TaskAssignment.expire_all_abandoned()
        self.assertEqual(TaskAssignment.objects.count(), 0)

    def test_expire_all_abandoned__dont_delete_completed(self):
        t = timezone.now()
        dt = datetime.timedelta(hours=2)
        past = t - dt

        project = Project(login_required=False)
        project.save()
        batch = Batch(
            allotted_assignment_time=1,
            project=project
        )
        batch.save()
        task = Task(batch=batch)
        task.save()
        ha = TaskAssignment(
            completed=True,
            expires_at=past,
            task=task,
        )
        # Bypass TaskAssignment's save(), which updates expires_at
        super(TaskAssignment, ha).save()
        self.assertEqual(TaskAssignment.objects.count(), 1)
        TaskAssignment.expire_all_abandoned()
        self.assertEqual(TaskAssignment.objects.count(), 1)

    def test_expire_all_abandoned__dont_delete_non_expired(self):
        t = timezone.now()
        dt = datetime.timedelta(hours=2)
        future = t + dt

        project = Project(login_required=False)
        project.save()
        batch = Batch(
            allotted_assignment_time=1,
            project=project
        )
        batch.save()
        task = Task(batch=batch)
        task.save()
        ha = TaskAssignment(
            completed=False,
            expires_at=future,
            task=task,
        )
        # Bypass TaskAssignment's save(), which updates expires_at
        super(TaskAssignment, ha).save()
        self.assertEqual(TaskAssignment.objects.count(), 1)
        TaskAssignment.expire_all_abandoned()
        self.assertEqual(TaskAssignment.objects.count(), 1)


class TestBatch(django.test.TestCase):

    def test_batch_to_csv(self):
        project = Project(name='test', html_template='<p>${number} - ${letter}</p>')
        project.save()
        batch = Batch(project=project)
        batch.save()

        task1 = Task(
            batch=batch,
            completed=True,
            input_csv_fields={'number': '1', 'letter': 'a'},
        )
        task1.save()
        TaskAssignment(
            answers={'combined': '1a'},
            assigned_to=None,
            completed=True,
            task=task1
        ).save()

        task2 = Task(
            batch=batch,
            completed=True,
            input_csv_fields={'number': '2', 'letter': 'b'},
        )
        task2.save()
        TaskAssignment(
            answers={'combined': '2b'},
            assigned_to=None,
            completed=True,
            task=task2
        ).save()

        csv_output = StringIO()
        batch.to_csv(csv_output)
        csv_string = csv_output.getvalue()
        self.assertTrue(b'"Input.letter","Input.number","Answer.combined"\r\n' in csv_string)
        self.assertTrue(b'"b","2","2b"\r\n' in csv_string)
        self.assertTrue(b'"a","1","1a"\r\n' in csv_string)

    def test_batch_to_csv_variable_number_of_answers(self):
        project = Project(name='test', html_template='<p>${letter}</p>')
        project.save()
        batch = Batch(project=project)
        batch.save()

        task1 = Task(
            batch=batch,
            completed=True,
            input_csv_fields={'letter': 'a'},
        )
        task1.save()
        TaskAssignment(
            answers={'1': 1, '2': 2},
            assigned_to=None,
            completed=True,
            task=task1,
        ).save()

        task2 = Task(
            batch=batch,
            completed=True,
            input_csv_fields={'letter': 'b'},
        )
        task2.save()
        TaskAssignment(
            answers={'3': 3, '4': 4},
            assigned_to=None,
            completed=True,
            task=task2
        ).save()

        task3 = Task(
            batch=batch,
            completed=True,
            input_csv_fields={'letter': 'c'},
        )
        task3.save()
        TaskAssignment(
            answers={'3': 3, '2': 2},
            assigned_to=None,
            completed=True,
            task=task3
        ).save()

        csv_output = StringIO()
        batch.to_csv(csv_output)
        rows = csv_output.getvalue().split()
        self.assertTrue(b'"Input.letter","Answer.1","Answer.2","Answer.3","Answer.4"' in rows[0])
        self.assertTrue(any([b'"a","1","2","",""' in row for row in rows[1:]]))
        self.assertTrue(any([b'"b","","","3","4"' in row for row in rows[1:]]))
        self.assertTrue(any([b'"c","","2","3",""' in row for row in rows[1:]]))

    def test_batch_to_csv_partially_completed_task(self):
        project = Project.objects.create(
            name='test', html_template='<p>${number} - ${letter}</p>')
        batch = Batch.objects.create(
            assignments_per_task=2,
            project=project
        )
        task = Task.objects.create(
            batch=batch,
            input_csv_fields={'number': '1', 'letter': 'a'},
        )

        csv_output = StringIO()
        batch.to_csv(csv_output)
        rows = csv_output.getvalue().splitlines()
        self.assertEqual(len(rows), 1)

        # Task Assignments that have not been completed should not generate CSV lines
        ta = TaskAssignment.objects.create(
            answers={'combined': '1a'},
            task=task,
        )
        csv_output = StringIO()
        batch.to_csv(csv_output)
        rows = csv_output.getvalue().splitlines()
        self.assertEqual(len(rows), 1)

        ta.completed = True
        ta.save()
        csv_output = StringIO()
        batch.to_csv(csv_output)
        rows = csv_output.getvalue().splitlines()
        self.assertEqual(len(rows), 2)

        TaskAssignment.objects.create(
            answers={'combined': '1a'},
            completed=True,
            task=task,
        )
        csv_output = StringIO()
        batch.to_csv(csv_output)
        rows = csv_output.getvalue().splitlines()
        self.assertEqual(len(rows), 3)

    def test_batch_from_emoji_csv(self):
        project = Project(name='test', html_template='<p>${emoji} - ${more_emoji}</p>')
        project.save()
        batch = Batch(project=project)
        batch.save()

        csv_fh = open(os.path.abspath('turkle/tests/resources/emoji.csv'), 'rb')
        batch.create_tasks_from_csv(csv_fh)

        self.assertEqual(batch.total_tasks(), 3)
        tasks = batch.task_set.all()
        self.assertEqual(tasks[0].input_csv_fields['emoji'], u'😀')
        self.assertEqual(tasks[0].input_csv_fields['more_emoji'], u'😃')
        self.assertEqual(tasks[2].input_csv_fields['emoji'], u'🤔')
        self.assertEqual(tasks[2].input_csv_fields['more_emoji'], u'🤭')

    def test_login_required_validation_1(self):
        # No ValidationError thrown
        project = Project(
            login_required=False,
        )
        project.save()
        Batch(
            assignments_per_task=1,
            project=project,
        ).clean()

    def test_login_required_validation_2(self):
        # No ValidationError thrown
        project = Project(
            login_required=True,
        )
        project.save()
        Batch(
            assignments_per_task=2,
            project=project,
        ).clean()

    def test_login_required_validation_3(self):
        with self.assertRaisesMessage(ValidationError, 'Assignments per Task must be 1'):
            project = Project(
                login_required=False,
            )
            project.save()
            Batch(
                assignments_per_task=2,
                project=project,
            ).clean()


class TestBatchAvailableTASKs(django.test.TestCase):
    def setUp(self):
        self.user = User.objects.create_user('testuser', password='secret')

        self.project = Project(name='test', html_template='<p>${number} - ${letter}</p>')
        self.project.save()

    def test_available_tasks_for__aph_is_1(self):
        batch = Batch(
            assignments_per_task=1,
            project=self.project
        )
        batch.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 0)
        self.assertEqual(batch.next_available_task_for(self.user), None)

        task = Task(
            batch=batch,
        )
        task.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 1)
        self.assertEqual(batch.next_available_task_for(self.user), task)

        task_assignment = TaskAssignment(
            assigned_to=self.user,
            completed=False,
            task=task,
        )
        task_assignment.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 0)
        self.assertEqual(batch.next_available_task_for(self.user), None)

    def test_available_tasks_for__aph_is_2(self):
        batch = Batch(
            assignments_per_task=2,
            project=self.project
        )
        batch.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 0)

        task = Task(
            batch=batch,
        )
        task.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 1)

        task_assignment = TaskAssignment(
            assigned_to=self.user,
            completed=False,
            task=task,
        )
        task_assignment.save()
        self.assertEqual(batch.total_available_tasks_for(self.user), 0)

        other_user = User.objects.create_user('other_user', password='secret')
        self.assertEqual(batch.total_available_tasks_for(other_user), 1)

        task_assignment = TaskAssignment(
            assigned_to=other_user,
            completed=False,
            task=task,
        )
        task_assignment.save()
        self.assertEqual(batch.total_available_tasks_for(other_user), 0)

    def test_available_tasks_for_anon_user(self):
        anonymous_user = AnonymousUser()
        user = User.objects.create_user('user', password='secret')

        project_protected = Project(
            active=True,
            login_required=True,
        )
        project_protected.save()
        self.assertEqual(len(Project.all_available_for(anonymous_user)), 0)
        self.assertEqual(len(Project.all_available_for(user)), 2)  # Project created by setUp
        batch_protected = Batch(project=project_protected)
        batch_protected.save()
        Task(batch=batch_protected).save()
        self.assertEqual(len(batch_protected.available_tasks_for(anonymous_user)), 0)
        self.assertEqual(len(batch_protected.available_tasks_for(user)), 1)

        project_unprotected = Project(
            active=True,
            login_required=False,
        )
        project_unprotected.save()
        batch_unprotected = Batch(project=project_unprotected)
        batch_unprotected.save()
        Task(batch=batch_unprotected).save()
        self.assertEqual(len(Project.all_available_for(anonymous_user)), 1)
        self.assertEqual(len(Project.all_available_for(user)), 3)
        self.assertEqual(len(project_unprotected.batches_available_for(anonymous_user)), 1)
        self.assertEqual(len(project_unprotected.batches_available_for(user)), 1)
        self.assertEqual(len(batch_unprotected.available_tasks_for(anonymous_user)), 1)
        self.assertEqual(len(batch_unprotected.available_tasks_for(user)), 1)


class TestBatchExpireAssignments(django.test.TestCase):
    def test_batch_expire_assignments(self):
        t = timezone.now()
        dt = datetime.timedelta(hours=2)
        past = t - dt

        project = Project(login_required=False)
        project.save()
        batch = Batch(
            allotted_assignment_time=1,
            project=project
        )
        batch.save()
        task = Task(batch=batch)
        task.save()
        ha = TaskAssignment(
            completed=False,
            expires_at=past,
            task=task,
        )
        # Bypass TaskAssignment's save(), which updates expires_at
        super(TaskAssignment, ha).save()
        self.assertEqual(TaskAssignment.objects.count(), 1)
        batch.expire_assignments()
        self.assertEqual(TaskAssignment.objects.count(), 0)


class TestProject(django.test.TestCase):

    def test_available_for_active_flag(self):
        user = User.objects.create_user('testuser', password='secret')

        self.assertEqual(len(Project.all_available_for(user)), 0)

        Project(
            active=False,
        ).save()
        self.assertEqual(len(Project.all_available_for(user)), 0)

        Project(
            active=True,
        ).save()
        self.assertEqual(len(Project.all_available_for(user)), 1)

    def test_available_for_login_required(self):
        anonymous_user = AnonymousUser()

        self.assertEqual(len(Project.all_available_for(anonymous_user)), 0)

        Project(
            login_required=True,
        ).save()
        self.assertEqual(len(Project.all_available_for(anonymous_user)), 0)

        authenticated_user = User.objects.create_user('testuser', password='secret')
        self.assertEqual(len(Project.all_available_for(authenticated_user)), 1)

    def test_batches_available_for(self):
        user = User.objects.create_user('testuser', password='secret')

        project = Project(
            active=True,
        )
        project.save()
        self.assertEqual(len(project.batches_available_for(user)), 0)

        Batch(
            active=False,
            project=project,
        ).save()
        self.assertEqual(len(project.batches_available_for(user)), 0)

        Batch(
            active=True,
            project=project,
        ).save()
        self.assertEqual(len(project.batches_available_for(user)), 1)

    def test_batches_available_for_anon(self):
        anonymous_user = AnonymousUser()

        project_protected = Project(
            active=True,
            login_required=True,
        )
        project_protected.save()
        self.assertEqual(len(project_protected.batches_available_for(anonymous_user)), 0)

        Batch(project=project_protected).save()
        self.assertEqual(len(project_protected.batches_available_for(anonymous_user)), 0)

        project_unprotected = Project(
            active=True,
            login_required=False,
        )
        project_unprotected.save()
        self.assertEqual(len(project_unprotected.batches_available_for(anonymous_user)), 0)

        Batch(project=project_unprotected).save()
        self.assertEqual(len(project_unprotected.batches_available_for(anonymous_user)), 1)

    def test_form_with_submit_button(self):
        project = Project(
            html_template='<p><input id="my_submit_button" type="submit" value="MySubmit" /></p>'
        )
        project.save()
        self.assertTrue(project.html_template_has_submit_button)

    def test_form_without_submit_button(self):
        project = Project(
            html_template='<p>Quick brown fox</p>'
        )
        project.save()
        self.assertFalse(project.html_template_has_submit_button)

    def test_group_permissions(self):
        user = User.objects.create_user('testuser', password='secret')
        group = Group.objects.create(name='testgroup')
        user.groups.add(group)
        project = Project()
        project.save()

        # Group permissions are ignored if custom_permissions is False
        self.assertTrue(project.available_for(user))
        project.custom_permissions = True
        project.save()
        self.assertFalse(project.available_for(user))

        # Verify that giving the group access also gives the group members access
        self.assertFalse(user.has_perm('can_work_on', project))
        assign_perm('can_work_on', group, project)
        self.assertTrue(user.has_perm('can_work_on', project))
        self.assertTrue(project.available_for(user))

    def test_login_required_validation_1(self):
        # No ValidationError thrown
        Project(
            assignments_per_task=1,
            login_required=False,
        ).clean()

    def test_login_required_validation_2(self):
        # No ValidationError thrown
        Project(
            assignments_per_task=2,
            login_required=True,
        ).clean()

    def test_login_required_validation_3(self):
        with self.assertRaisesMessage(ValidationError, 'Assignments per Task must be 1'):
            Project(
                assignments_per_task=2,
                login_required=False,
            ).clean()


class TestModels(django.test.TestCase):

    def setUp(self):
        """
        Sets up Project, Task objects, and saves them to the DB.
        The Project form HTML only displays the one input variable.
        The Task has inputs and answers and refers to the Project form.
        """
        project = Project(name='test', html_template="<p>${foo}</p>")
        project.save()
        batch = Batch(project=project)
        batch.save()

        task = Task(
            batch=batch,
            input_csv_fields={u'foo': u'bar'},
            completed=True,
        )
        task.save()
        self.task = task

        self.task_assignment = TaskAssignment(
            answers={
                u"comment": u"\u221e", u"userDisplayLanguage": u"",
                u"sentence_textbox_3_verb1": u"", u"city": u"",
                u"sentence_textbox_1_verb6": u"",
                u"sentence_textbox_1_verb7": u"",
                u"sentence_textbox_1_verb4": u"",
                u"sentence_textbox_1_verb5": u"",
                u"sentence_textbox_1_verb2": u"",
                u"sentence_textbox_1_verb3": u"",
                u"sentence_textbox_1_verb1": u"",
                u"sentence_textbox_2_verb4": u"",
                u"csrfmiddlewaretoken": u"7zxQ9Yyug6Nsnm4nLky9p8ObJwNipdu8",
                u"sentence_drop_2_verb3": u"foo",
                u"sentence_drop_2_verb2": u"foo",
                u"sentence_drop_2_verb1": u"foo",
                u"sentence_textbox_2_verb1": u"",
                u"sentence_textbox_2_verb3": u"",
                u"sentence_drop_2_verb4": u"foo",
                u"sentence_textbox_2_verb2": u"",
                u"submitit": u"Submit", u"browserInfo": u"",
                u"sentence_drop_1_verb1": u"foo",
                u"sentence_drop_1_verb2": u"foo",
                u"sentence_drop_1_verb3": u"foo",
                u"sentence_drop_1_verb4": u"foo",
                u"sentence_drop_1_verb5": u"foo",
                u"sentence_drop_1_verb6": u"foo",
                u"sentence_drop_1_verb7": u"foo", u"country": u"",
                u"sentence_drop_3_verb1": u"foo",
                u"ipAddress": u"", u"region": u""
            },
            assigned_to=None,
            completed=True,
            task=task
        )
        self.task_assignment.save()

    def test_extract_fieldnames_from_form_html(self):
        self.assertEqual(
            {u'foo': True},
            self.task.batch.project.fieldnames
        )

        project = Project(name='test', html_template='<p>${foo} - ${bar}</p>')
        project.save()
        self.assertEqual(
            {u'foo': True, u'bar': True},
            project.fieldnames
        )

    def test_project_to_csv(self):
        project = Project(name='test', html_template='<p>${number} - ${letter}</p>')
        project.save()
        batch_one = Batch(project=project)
        batch_one.save()

        task1 = Task(
            batch=batch_one,
            completed=True,
            input_csv_fields={'number': '1', 'letter': 'a'},
        )
        task1.save()
        TaskAssignment(
            answers={'combined': '1a'},
            assigned_to=None,
            completed=True,
            task=task1
        ).save()

        batch_two = Batch(project=project)
        batch_two.save()
        task2 = Task(
            batch=batch_two,
            completed=True,
            input_csv_fields={'number': '2', 'letter': 'b'}
        )
        task2.save()
        TaskAssignment(
            answers={'combined': '2b'},
            assigned_to=None,
            completed=True,
            task=task2
        ).save()

        csv_output = StringIO()
        project.to_csv(csv_output)

        rows = csv_output.getvalue().split(b'\r\n')
        self.assertTrue(
            b'"Input.letter","Input.number","Answer.combined"' in rows[0])
        self.assertTrue(any([b'"a","1","1a"' in row for row in rows[1:]]))
        self.assertTrue(any([b'"b","2","2b"' in row for row in rows[1:]]))

    def test_project_to_csv_different_answers_per_batch(self):
        project = Project(name='test', html_template='<p>${letter}</p>')
        project.save()
        batch_one = Batch(project=project)
        batch_one.save()

        task1 = Task(
            batch=batch_one,
            completed=True,
            input_csv_fields={'letter': 'a'},
        )
        task1.save()
        TaskAssignment(
            answers={'1': 1, '2': 2},
            assigned_to=None,
            completed=True,
            task=task1
        ).save()

        batch_two = Batch(project=project)
        batch_two.save()
        task2 = Task(
            batch=batch_two,
            completed=True,
            input_csv_fields={'letter': 'b'},
        )
        task2.save()
        TaskAssignment(
            answers={'3': 3, '4': 4},
            assigned_to=None,
            completed=True,
            task=task2
        ).save()

        csv_output = StringIO()
        project.to_csv(csv_output)

        rows = csv_output.getvalue().split(b'\r\n')
        self.assertTrue(
            b'"Input.letter","Answer.1","Answer.2","Answer.3","Answer.4"' in rows[0])
        self.assertTrue(b'"a","1","2","",""' in rows[1])
        self.assertTrue(b'"b","","","3","4"' in rows[2])

    def test_new_task(self):
        """
        unicode(task) should return the template's title followed by :id of the
        task.
        """
        self.assertEqual('Task id:1', unicode(self.task))

    def test_result_to_dict_Answer(self):
        self.assertEqual(
            'foo',
            self.task_assignment.answers['sentence_drop_1_verb1']
        )

    def test_result_to_dict_ignore_csrfmiddlewaretoken(self):
        with self.assertRaises(KeyError):
            self.task_assignment.answers['Answer.csrfmiddlewaretoken']

    def test_result_to_dict_should_include_inputs(self):
        self.assertEqual(
            'foo',
            self.task_assignment.answers['sentence_drop_1_verb1']
        )

    def test_result_to_dict_unicode(self):
        self.assertEqual(
            u'∞',
            self.task_assignment.answers['comment']
        )


class TestGenerateForm(django.test.TestCase):

    def setUp(self):
        with open('turkle/tests/resources/form_0.html') as f:
            html_template = f.read()
            # python 2 compat hack
            try:
                html_template = html_template.decode('utf-8')
            except AttributeError:
                pass

        self.project = Project(name="filepath", html_template=html_template)
        self.project.save()
        self.batch = Batch(project=self.project)
        self.batch.save()
        field_names = u"tweet0_id,tweet0_entity,tweet0_before_entity,tweet0_after_entity," + \
            u"tweet0_word0,tweet0_word1,tweet0_word2,tweet1_id,tweet1_entity," + \
            u"tweet1_before_entity,tweet1_after_entity,tweet1_word0,tweet1_word1,tweet1_word2," + \
            u"tweet2_id,tweet2_entity,tweet2_before_entity,tweet2_after_entity,tweet2_word0," + \
            u"tweet2_word1,tweet2_word2,tweet3_id,tweet3_entity,tweet3_before_entity," + \
            u"tweet3_after_entity,tweet3_word0,tweet3_word1,tweet3_word2,tweet4_id," + \
            u"tweet4_entity,tweet4_before_entity,tweet4_after_entity,tweet4_word0," + \
            u"tweet4_word1,tweet4_word2,tweet5_id,tweet5_entity,tweet5_before_entity," + \
            u"tweet5_after_entity,tweet5_word0,tweet5_word1,tweet5_word2",
        values = u"268,SANTOS, Muy bien America ......... y lo siento mucho , un muy buen " + \
            u"rival,mucho,&nbsp;,&nbsp;,2472,GREGORY, Ah bueno , tampoco andes pidiendo ese " +\
            u"tipo de milagros . @jcabrerac @CarlosCabreraR,bueno,&nbsp;,&nbsp;,478,ALEJANDRO," + \
            u" @aguillen19 &#44; un super abrazo mi querido , &#44; mis mejores deseos para " + \
            u"este 2012 ... muakkk !,querido,&nbsp;,&nbsp;,906_control, PF, Acusan camioneros " + \
            u"extorsiones de, : Transportistas acusaron que deben pagar entre 13 y 15 mil " + \
            u"pesos a agentes que .. http://t.co/d8LUVvhP,acusaron,&nbsp;,&nbsp;,2793_control," + \
            u" CHICARO, Me gusta cuando chicharo hace su oracion es lo que lo hace especial .," + \
            u"&nbsp;,gusta,&nbsp;,&nbsp;,357,OSCAR WILDE&QUOT;, &quot; @ ifilosofia : Las " + \
            u"pequeñas acciones de cada día son las que hacen o deshacen el carácter.&quot; , " + \
            u"bueno !!!! Es así,bueno,&nbsp;,&nbsp;",
        self.task = Task(
            batch=self.batch,
            input_csv_fields=dict(zip(field_names, values))
        )
        self.task.save()

    def test_populate_html_template(self):
        with open('turkle/tests/resources/form_0_filled.html') as f:
            form = f.read()
            # python 2 compat hack
            try:
                form = form.decode('utf-8')
            except AttributeError:
                pass

        expect = form
        actual = self.task.populate_html_template()
        self.assertNotEqual(expect, actual)

    def test_map_fields_csv_row(self):
        project = Project(
            name='test',
            html_template=u"""</select> con relaci&oacute;n a """ +
            u"""<span style="color: rgb(0, 0, 255);">""" +
            u"""${tweet0_entity}</span> en este mensaje.</p>"""
        )
        project.save()
        batch = Batch(project=project)
        batch.save()
        task = Task(
            batch=batch,
            input_csv_fields=dict(
                zip(
                    [u"tweet0_id", u"tweet0_entity"],
                    [u"268", u"SANTOS"],
                )
            ),
        )
        task.save()
        expect = u"""</select> con relaci&oacute;n a <span style="color:""" + \
            u""" rgb(0, 0, 255);">SANTOS</span> en este mensaje.</p>"""
        actual = task.populate_html_template()
        self.assertEqual(expect, actual)


__all__ = (
    'TestGenerateForm',
    'TestModels',
)
