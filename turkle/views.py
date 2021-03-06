try:
    from cStringIO import StringIO
except ImportError:
    try:
        from StringIO import StringIO
    except ImportError:
        from io import BytesIO
        StringIO = BytesIO

# hack to add unicode() to python3 for backward compatibility
try:
    unicode('')
except NameError:
    unicode = str

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.utils import OperationalError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from functools import wraps

from turkle.models import Task, TaskAssignment, Batch, Project


def handle_db_lock(func):
    """Decorator that catches database lock errors from sqlite"""
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            return func(request, *args, **kwargs)
        except OperationalError as ex:
            # sqlite3 cannot handle concurrent transactions.
            # This should be very rare with just a few users.
            # If it happens often, switch to mysql or postgres.
            if str(ex) == 'database is locked':
                messages.error(request, u'The database is busy. Please try again.')
                return redirect(index)
            raise ex
    return wrapper


@handle_db_lock
def accept_task(request, batch_id, task_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    try:
        with transaction.atomic():
            # Lock access to the specified Task
            Task.objects.filter(id=task_id).select_for_update()

            # Will throw ObjectDoesNotExist exception if Task no longer available
            batch.available_tasks_for(request.user).get(id=task_id)

            ha = TaskAssignment()
            if request.user.is_authenticated:
                ha.assigned_to = request.user
            else:
                ha.assigned_to = None
            ha.task = task
            ha.save()
    except ObjectDoesNotExist:
        messages.error(request, u'The Task with ID {} is no longer available'.format(task_id))
        return redirect(index)

    return redirect(task_assignment, task.id, ha.id)


@handle_db_lock
def accept_next_task(request, batch_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        with transaction.atomic():
            batch = Batch.objects.get(id=batch_id)

            # Lock access to all Tasks available to current user in the batch
            batch.available_task_ids_for(request.user).select_for_update()

            task_id = _skip_aware_next_available_task_id(request, batch)

            if task_id:
                ha = TaskAssignment()
                if request.user.is_authenticated:
                    ha.assigned_to = request.user
                else:
                    ha.assigned_to = None
                ha.task_id = task_id
                ha.save()
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    if task_id:
        return redirect(task_assignment, task_id, ha.id)
    else:
        messages.error(request, u'No more Tasks available from Batch {}'.format(batch_id))
        return redirect(index)


@staff_member_required
def download_batch_csv(request, batch_id):
    """
    Security behavior:
    - Access to this page is limited to requesters.  Any requester can
      download any CSV file.
    """
    batch = Batch.objects.get(id=batch_id)
    csv_output = StringIO()
    if request.session.get('csv_unix_line_endings', False):
        batch.to_csv(csv_output, lineterminator='\n')
    else:
        batch.to_csv(csv_output)
    csv_string = csv_output.getvalue()
    response = HttpResponse(csv_string, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="{}"'.format(
        batch.csv_results_filename())
    return response


def task_assignment(request, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != task_assignment.assigned_to:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)
    else:
        if task_assignment.assigned_to is not None:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)

    auto_accept_status = request.session.get('auto_accept_status', False)

    if request.method == 'GET':
        return render(
            request,
            'task_assignment.html',
            {
                'auto_accept_status': auto_accept_status,
                'task': task,
                'task_assignment': task_assignment,
            },
        )
    else:
        task_assignment.answers = dict(request.POST.items())
        task_assignment.completed = True
        task_assignment.save()

        if request.session.get('auto_accept_status'):
            return redirect(accept_next_task, task.batch.id)
        else:
            return redirect(index)


def task_assignment_iframe(request, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error messge.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != task_assignment.assigned_to:
            messages.error(
                request,
                u'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)

    return render(
        request,
        'task_assignment_iframe.html',
        {
            'task': task,
            'task_assignment': task_assignment,
        },
    )


def index(request):
    """
    Security behavior:
    - Anyone can access the page, but the page only shows the user
      information they have access to.
    """
    abandoned_assignments = []
    if request.user.is_authenticated:
        for ha in TaskAssignment.objects.filter(assigned_to=request.user).filter(completed=False):
            abandoned_assignments.append({
                'task': ha.task,
                'task_assignment_id': ha.id
            })

    # Create a row for each Batch that has Tasks available for the current user
    batch_rows = []
    for project in Project.all_available_for(request.user):
        for batch in project.batches_available_for(request.user):
            total_tasks_available = batch.total_available_tasks_for(request.user)
            if total_tasks_available > 0:
                batch_rows.append({
                    'project_name': project.name,
                    'batch_name': batch.name,
                    'batch_published': batch.created_at,
                    'assignments_available': total_tasks_available,
                    'preview_next_task_url': reverse('preview_next_task',
                                                     kwargs={'batch_id': batch.id}),
                    'accept_next_task_url': reverse('accept_next_task',
                                                    kwargs={'batch_id': batch.id})
                })
    return render(request, 'index.html', {
        'abandoned_assignments': abandoned_assignments,
        'batch_rows': batch_rows
    })


def preview(request, task_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    if not task.batch.project.available_for(request.user):
        messages.error(request, u'You do not have permission to view this Task')
        return redirect(index)

    return render(request, 'preview.html', {'task': task})


def preview_iframe(request, task_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    if not task.batch.project.available_for(request.user):
        messages.error(request, u'You do not have permission to view this Task')
        return redirect(index)

    return render(request, 'preview_iframe.html', {'task': task})


def preview_next_task(request, batch_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    task_id = _skip_aware_next_available_task_id(request, batch)

    if task_id:
        return redirect(preview, task_id)
    else:
        messages.error(request,
                       u'No more Tasks are available for Batch "{}"'.format(batch.name))
        return redirect(index)


def return_task_assignment(request, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error
    return redirect(index)


def skip_and_accept_next_task(request, batch_id, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error

    _add_task_id_to_skip_session(request.session, batch_id, task_id)
    return redirect(accept_next_task, batch_id)


def skip_task(request, batch_id, task_id):
    """
    Security behavior:
    - This view updates a session variable that controls the order
      that Tasks are presented to a user.  Users cannot modify other
      users session variables.
    """
    _add_task_id_to_skip_session(request.session, batch_id, task_id)
    return redirect(preview_next_task, batch_id)


def update_auto_accept(request):
    """
    Security behavior:
    - This view updates a session variable that controls whether or
      not Task Assignments are auto-accepted.  Users cannot modify other
      users session variables.
    """
    accept_status = (request.POST[u'auto_accept'] == u'true')
    request.session['auto_accept_status'] = accept_status
    return JsonResponse({})


def _add_task_id_to_skip_session(session, batch_id, task_id):
    """Add Task ID to session variable tracking Tasks the user has skipped
    """
    # The Django session store converts dictionary keys from ints to strings
    batch_id = unicode(batch_id)
    task_id = unicode(task_id)

    if 'skipped_tasks_in_batch' not in session:
        session['skipped_tasks_in_batch'] = {}
    if batch_id not in session['skipped_tasks_in_batch']:
        session['skipped_tasks_in_batch'][batch_id] = []
        session.modified = True
    if task_id not in session['skipped_tasks_in_batch'][batch_id]:
        session['skipped_tasks_in_batch'][batch_id].append(task_id)
        session.modified = True


@handle_db_lock
def _delete_task_assignment(request, task_id, task_assignment_id):
    """Delete a TaskAssignment, if possible

    Returns:
        - None if the TaskAssignment can be deleted, *OR*
        - An HTTPResponse object created by redirect() if there was an error

    Usage:
        redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
        if redirect_due_to_error:
            return redirect_due_to_error
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       u'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if task_assignment.completed:
        messages.error(request, u"The Task can't be returned because it has been completed")
        return redirect(index)
    if request.user.is_authenticated:
        if task_assignment.assigned_to != request.user:
            messages.error(request, u'The Task you are trying to return belongs to another user')
            return redirect(index)
    else:
        if task_assignment.assigned_to is not None:
            messages.error(request, u'The Task you are trying to return belongs to another user')
            return redirect(index)
        if task.batch.project.login_required:
            messages.error(request, u'You do not have permission to access this Task')
            return redirect(index)

    with transaction.atomic():
        # Lock access to the specified Task
        Task.objects.filter(id=task_id).select_for_update()

        task_assignment.delete()


def _skip_aware_next_available_task_id(request, batch):
    """Get next available Task for user, taking into account previously skipped Tasks

    This function will first look for an available Task that the user
    has not previously skipped.  If the only available Tasks are Tasks
    that the user has skipped, this function will return the first
    such Task.

    Returns:
        Task ID (int), or None if no more Tasks are available
    """
    def _get_skipped_task_ids_for_batch(session, batch_id):
        batch_id = unicode(batch_id)
        if 'skipped_tasks_in_batch' in session and \
           batch_id in session['skipped_tasks_in_batch']:
            return session['skipped_tasks_in_batch'][batch_id]
        else:
            return None

    available_task_ids = batch.available_task_ids_for(request.user)
    skipped_ids = _get_skipped_task_ids_for_batch(request.session, batch.id)

    if skipped_ids:
        task_id = available_task_ids.exclude(id__in=skipped_ids).first()
        if not task_id:
            task_id = available_task_ids.filter(id__in=skipped_ids).first()
            if task_id:
                messages.info(request, u'Only previously skipped Tasks are available')

                # Once all remaining Tasks have been marked as skipped, we clear
                # their skipped status.  If we don't take this step, then a Task
                # cannot be skipped a second time.
                request.session['skipped_tasks_in_batch'][unicode(batch.id)] = []
                request.session.modified = True
    else:
        task_id = available_task_ids.first()

    return task_id
