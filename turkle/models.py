import datetime
import os.path
import re
import sys

from bs4 import BeautifulSoup
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from jsonfield import JSONField
import unicodecsv


# The default field size limit is 131072 characters
unicodecsv.field_size_limit(sys.maxsize)


class Task(models.Model):
    """Human Intelligence Task
    """
    class Meta:
        verbose_name = "Task"

    batch = models.ForeignKey('Batch', on_delete=models.CASCADE)
    completed = models.BooleanField(default=False)
    input_csv_fields = JSONField()

    def __unicode__(self):
        return 'Task id:{}'.format(self.id)

    def __str__(self):
        return 'Task id:{}'.format(self.id)

    def populate_html_template(self):
        """Return HTML template for this Task's project, with populated template variables

        Returns:
            String containing the HTML template for the Project associated with
            this Task, with all template variables replaced with the template
            variable values stored in this Task's input_csv_fields.
        """
        result = self.batch.project.html_template
        for field in self.input_csv_fields.keys():
            result = result.replace(
                r'${' + field + r'}',
                self.input_csv_fields[field]
            )
        return result


class TaskAssignment(models.Model):
    """Task Assignment
    """
    class Meta:
        verbose_name = "Task Assignment"

    answers = JSONField(blank=True)
    assigned_to = models.ForeignKey(User, db_index=True, null=True, on_delete=models.CASCADE)
    completed = models.BooleanField(db_index=True, default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True)
    task = models.ForeignKey(Task, on_delete=models.CASCADE)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def expire_all_abandoned(cls):
        return cls.objects.\
            filter(completed=False).\
            filter(expires_at__lt=timezone.now()).\
            delete()

    def save(self, *args, **kwargs):
        self.expires_at = timezone.now() + \
            datetime.timedelta(hours=self.task.batch.allotted_assignment_time)

        if 'csrfmiddlewaretoken' in self.answers:
            del self.answers['csrfmiddlewaretoken']
        super(TaskAssignment, self).save(*args, **kwargs)

        # Mark Task as completed if all Assignments have been completed
        if self.task.taskassignment_set.filter(completed=True).count() >= \
           self.task.batch.assignments_per_task:
            self.task.completed = True
            self.task.save()


class Batch(models.Model):
    class Meta:
        verbose_name = "Batch"
        verbose_name_plural = "Batches"

    active = models.BooleanField(db_index=True, default=True)
    allotted_assignment_time = models.IntegerField(default=24)
    assignments_per_task = models.IntegerField(default=1, verbose_name='Assignments per Task')
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True)
    filename = models.CharField(max_length=1024)
    project = models.ForeignKey('Project', on_delete=models.CASCADE)
    name = models.CharField(max_length=1024)

    def available_tasks_for(self, user):
        """Retrieve a list of all Tasks in this batch available for the user.

        This list DOES NOT include Tasks in the batch that have been assigned
        to the user but not yet completed.

        Args:
            user (User|AnonymousUser):

        Returns:
            QuerySet of Task objects
        """
        if not user.is_authenticated and self.project.login_required:
            return Task.objects.none()

        hs = self.task_set.filter(completed=False)

        # Exclude Tasks that have already been assigned to this user.
        if user.is_authenticated:
            # If the user is not authenticated, then user.id is None,
            # and the query below would exclude all uncompleted Tasks.
            hs = hs.exclude(taskassignment__assigned_to_id=user.id)

        # Only include Tasks whose total (possibly incomplete) assignments < assignments_per_task
        hs = hs.annotate(ac=models.Count('taskassignment')).\
            filter(ac__lt=self.assignments_per_task)

        return hs

    def available_task_ids_for(self, user):
        return self.available_tasks_for(user).values_list('id', flat=True)

    def clean(self):
        # Without this guard condition for project_id, a
        # RelatedObjectDoesNotExist exception is thrown before a
        # ValidationError can be thrown.  When this model is edited
        # using a form, this causes the server to generate an HTTP 500
        # error due to the uncaught RelatedObjectDoesNotExist
        # exception, instead of catching the ValidationError and
        # displaying a form with a "Field required" warning.
        if self.project_id:
            if not self.project.login_required and self.assignments_per_task != 1:
                raise ValidationError('When login is not required to access a Project, ' +
                                      'the number of Assignments per Task must be 1')

    def csv_results_filename(self):
        """Returns filename for CSV results file for this Batch
        """
        batch_filename, extension = os.path.splitext(os.path.basename(self.filename))

        # We are following Mechanical Turk's naming conventions for results files
        return "{}-Batch_{}_results{}".format(batch_filename, self.id, extension)

    def create_tasks_from_csv(self, csv_fh):
        """
        Args:
            csv_fh (file-like object): File handle for CSV input

        Returns:
            Number of Tasks created from CSV file
        """
        header, data_rows = self._parse_csv(csv_fh)

        num_created_tasks = 0
        for row in data_rows:
            if not row:
                continue
            task = Task(
                batch=self,
                input_csv_fields=dict(zip(header, row)),
            )
            task.save()
            num_created_tasks += 1

        return num_created_tasks

    def expire_assignments(self):
        TaskAssignment.objects.\
            filter(completed=False).\
            filter(task__batch_id=self.id).\
            filter(expires_at__lt=timezone.now()).\
            delete()

    def finished_tasks(self):
        """
        Returns:
            QuerySet of all Task objects associated with this Batch
            that have been completed.
        """
        return self.task_set.filter(completed=True).order_by('-id')

    def finished_task_assignments(self):
        """
        Returns:
            QuerySet of all Task Assignment objects associated with this Batch
            that have been completed.
        """
        return TaskAssignment.objects.filter(task__batch_id=self.id)\
                                     .filter(completed=True)

    def next_available_task_for(self, user):
        """Returns next available Task for the user, or None if no Tasks available

        Args:
            user (User):

        Returns:
            Task|None
        """
        return self.available_tasks_for(user).first()

    def total_available_tasks_for(self, user):
        """Returns number of Tasks available for the user

        Args:
            user (User):

        Returns:
            Number of Tasks available for user
        """
        return self.available_tasks_for(user).count()

    def total_finished_tasks(self):
        return self.finished_tasks().count()
    total_finished_tasks.short_description = 'Total finished Tasks'

    def total_finished_task_assignments(self):
        return self.finished_task_assignments().count()
    total_finished_task_assignments.short_description = 'Total finished Task Assignments'

    def total_tasks(self):
        return self.task_set.count()
    total_tasks.short_description = 'Total Tasks'

    def to_csv(self, csv_fh, lineterminator='\r\n'):
        """Write CSV output to file handle for every Task in batch

        Args:
            csv_fh (file-like object): File handle for CSV output
        """
        fieldnames, rows = self._results_data(self.task_set.all())
        writer = unicodecsv.DictWriter(csv_fh, fieldnames, lineterminator=lineterminator,
                                       quoting=unicodecsv.QUOTE_ALL)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    def unfinished_tasks(self):
        """
        Returns:
            QuerySet of all Task objects associated with this Batch
            that have NOT been completed.
        """
        return self.task_set.filter(completed=False).order_by('id')

    def _parse_csv(self, csv_fh):
        """
        Args:
            csv_fh (file-like object): File handle for CSV output

        Returns:
            A tuple where the first value is a list of strings for the
            header fieldnames, and the second value is an iterable
            that returns a list of values for the rest of the roww in
            the CSV file.
        """
        rows = unicodecsv.reader(csv_fh)
        header = next(rows)
        return header, rows

    def _get_csv_fieldnames(self, tasks):
        """
        Args:
            tasks (List of Task objects):

        Returns:
            A tuple of strings specifying the fieldnames to be used in
            in the header of a CSV file.
        """
        input_field_set = set()
        answer_field_set = set()
        for task in tasks:
            for task_assignment in task.taskassignment_set.all():
                input_field_set.update(task.input_csv_fields.keys())
                answer_field_set.update(task_assignment.answers.keys())
        return tuple(
            [u'HITId', u'HITTypeId', u'Title', u'CreationTime', u'MaxAssignments',
             u'AssignmentDurationInSeconds', u'AssignmentId', u'WorkerId',
             u'AcceptTime', u'SubmitTime', u'WorkTimeInSeconds'] +
            [u'Input.' + k for k in sorted(input_field_set)] +
            [u'Answer.' + k for k in sorted(answer_field_set)]
        )

    def _results_data(self, tasks):
        """
        All completed Tasks must come from the same project so that they have the
        same field names.

        Args:
            tasks (List of Task objects):

        Returns:
            A tuple where the first value is a list of fieldname strings, and
            the second value is a list of dicts, where the keys to these
            dicts are the values of the fieldname strings.
        """
        rows = []
        time_format = '%a %b %m %H:%M:%S %Z %Y'
        for task in tasks:
            for task_assignment in task.taskassignment_set.filter(completed=True):
                batch = task.batch
                project = task.batch.project

                row = {
                    'HITId': task.id,
                    'HITTypeId': project.id,
                    'Title': project.name,
                    'CreationTime': batch.created_at.strftime(time_format),
                    'MaxAssignments': batch.assignments_per_task,
                    'AssignmentDurationInSeconds': batch.allotted_assignment_time * 3600,
                    'AssignmentId': task_assignment.id,
                    'WorkerId': task_assignment.assigned_to_id,
                    'AcceptTime': task_assignment.created_at.strftime(time_format),
                    'SubmitTime': task_assignment.updated_at.strftime(time_format),
                    'WorkTimeInSeconds': int((task_assignment.updated_at -
                                              task_assignment.created_at).total_seconds()),
                }
                row.update({u'Input.' + k: v for k, v in task.input_csv_fields.items()})
                row.update({u'Answer.' + k: v for k, v in task_assignment.answers.items()})
                rows.append(row)

        return self._get_csv_fieldnames(tasks), rows

    def __unicode__(self):
        return 'Batch: {}'.format(self.name)

    def __str__(self):
        return 'Batch: {}'.format(self.name)


class Project(models.Model):
    class Meta:
        permissions = (
            ('can_work_on', 'Can work on Tasks for this Project'),
        )
        verbose_name = "Project"

    active = models.BooleanField(db_index=True, default=True)
    assignments_per_task = models.IntegerField(db_index=True, default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, related_name='created_projects')
    custom_permissions = models.BooleanField(default=False)
    filename = models.CharField(max_length=1024, blank=True)
    html_template = models.TextField()
    html_template_has_submit_button = models.BooleanField(default=False)
    login_required = models.BooleanField(db_index=True, default=True)
    name = models.CharField(max_length=1024)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, related_name='updated_projects')

    # Fieldnames are automatically extracted from html_template text
    fieldnames = JSONField(blank=True)

    @classmethod
    def all_available_for(cls, user):
        """Retrieve the Projects that the user has permission to access

        Args:
            user (User):

        Returns:
            QuerySet of Project objects this user can access
        """
        projects = cls.objects.filter(active=True)
        if not user.is_authenticated:
            projects = projects.filter(login_required=False)

        projects = [p for p in projects if p.available_for(user)]
        return projects

    def available_for(self, user):
        """
        Returns:
            Boolean indicating if this Project is available for the user
        """
        if not user.is_authenticated and self.login_required:
            return False
        elif self.custom_permissions:
            return user.has_perm('can_work_on', self)
        else:
            return True

    def batches_available_for(self, user):
        """Retrieve the Batches that the user has permission to access

        Args:
            user (User):

        Returns:
            QuerySet of Batch objects this usre can access
        """
        batches = self.batch_set.filter(active=True)
        if not user.is_authenticated:
            batches = batches.filter(project__login_required=False)
        return batches

    def clean(self):
        if not self.login_required and self.assignments_per_task != 1:
            raise ValidationError('When login is not required to access the Project, ' +
                                  'the number of Assignments per Task must be 1')

    def save(self, *args, **kwargs):
        soup = BeautifulSoup(self.html_template, 'html.parser')
        self.html_template_has_submit_button = bool(soup.select('input[type=submit]'))

        # Extract fieldnames from html_template text, save fieldnames as keys of JSON dict
        unique_fieldnames = set(re.findall(r'\${(\w+)}', self.html_template))
        self.fieldnames = dict((fn, True) for fn in unique_fieldnames)
        super(Project, self).save(*args, **kwargs)

    def to_csv(self, csv_fh, lineterminator='\r\n'):
        """
        Writes CSV output to file handle for every Task associated with project

        Args:
            csv_fh (file-like object): File handle for CSV output
        """
        batches = self.batch_set.all()
        if batches:
            fieldnames = self._get_csv_fieldnames(batches)
            writer = unicodecsv.DictWriter(csv_fh, fieldnames, lineterminator=lineterminator,
                                           quoting=unicodecsv.QUOTE_ALL)
            writer.writeheader()
            for batch in batches:
                _, rows = batch._results_data(batch.finished_tasks())
                for row in rows:
                    writer.writerow(row)

    def _get_csv_fieldnames(self, batches):
        """
        Args:
            batches (List of Batch objects)

        Returns:
            A tuple of strings specifying the fieldnames to be used in
            in the header of a CSV file.
        """
        input_field_set = set()
        answer_field_set = set()
        for batch in batches:
            for task in batch.task_set.all():
                for task_assignment in task.taskassignment_set.all():
                    input_field_set.update(task.input_csv_fields.keys())
                    answer_field_set.update(task_assignment.answers.keys())
        return tuple(
            [u'HITId', u'HITTypeId', u'Title', u'CreationTime', u'MaxAssignments',
             u'AssignmentDurationInSeconds', u'AssignmentId', u'WorkerId',
             u'AcceptTime', u'SubmitTime', u'WorkTimeInSeconds'] +
            [u'Input.' + k for k in sorted(input_field_set)] +
            [u'Answer.' + k for k in sorted(answer_field_set)]
        )

    def __unicode__(self):
        return 'Project: {}'.format(self.name)

    def __str__(self):
        return 'Project: {}'.format(self.name)
