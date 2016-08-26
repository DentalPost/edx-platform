"""
Fix StudentModule entries that have Course IDs with trailing newlines.

Due to a bug, many rows in courseware_studentmodule were written with a
course_id that had a trailing newline. This command tries to fix that, and to
merge that data with data that might have been written to the correct course_id.
"""
from collections import namedtuple
from optparse import make_option
from textwrap import dedent
import logging

from django.db import DatabaseError
from django.core.management.base import BaseCommand, CommandError

from courseware.models import StudentModule, BaseStudentModuleHistory
from util.query import use_read_replica_if_available

log = logging.getLogger("fix_student_module_newlines")

FixResult = namedtuple('FixResult', 'record_trimmed, data_copied, record_deleted, error')


class Command(BaseCommand):
    """Fix StudentModule entries that have Course IDs with trailing newlines."""
    args = "<start_date> <end_date>"
    help = dedent(__doc__).strip()
    option_list = BaseCommand.option_list + (
        make_option('--dry_run',
                    action='store_true',
                    default=False,
                    help="Run read querys and say what we're going to do, but don't actually do it."),
    )

    def handle(self, *args, **options):
        """Fix newline courses in CSM!"""
        if len(args) != 2:
            raise CommandError('Must specify start and end dates: e.g. "2016-08-23 16:43:00" "2016-08-24 22:00:00"')

        start, end = args
        dry_run = options['dry_run']

        log.info(
            "Starting fix_student_module_newlines in %s mode!",
            "dry_run" if dry_run else "real"
        )

        rows_to_fix = use_read_replica_if_available(
            StudentModule.objects.raw(
                "select * from courseware_studentmodule where modified between %s and %s and course_id like %s",
                (start, end, '%\n')
            )
        )

        results = [self.fix_row(row, dry_run=dry_run) for row in rows_to_fix]
        log.info(
            "Finished fix_student_module_newlines in %s mode!",
            "dry_run" if dry_run else "real"
        )
        log.info("Stats: %s rows detected", len(results))
        if results:
            # Add up all the columns
            aggregated_result = FixResult(*[sum(col) for col in zip(*results)])
            log.info("Results: %s", aggregated_result)


    def fix_row(self, read_only_newline_course_row, dry_run=False):
        """
        Fix a StudentModule with a trailing newline in the course_id.

        Returns a count of (row modified, )

        At the end of this method call, the record should no longer have a
        trailing newline for the course_id. There are three possible outcomes:

        1. There was never a conflicting entry:
            -> We just update this row.
        2. Conflict and the other row (with correct course_id) wins:
            -> We delete this row.
        2. Conflict and this row wins:
            -> We copy the data to the conflicting row (the one that has a
               correct course_id), and delete this row.

        Even though all the StudentModule entries coming in here have trailing
        newlines in the course_id, the deserialization logic will obscure that
        (it gets parsed out when read from the database). We will also
        automatically strip the newlines when writing back to the database, so
        we have to be very careful about violating unique constraints by doing
        unintended updates. That's why we only do an update to
        newline_course_row if no correct_course_row exists.
        """
        # We got the StudentModule from the read replica, so we have to fetch it
        # again from our writeable database before making changes
        try:
            newline_course_row = StudentModule.objects.get(id=read_only_newline_course_row.id)
        except StudentModule.DoesNotExist:
            # We're not going to be able to make any corrective writes, so just fail fast
            log.exception("Could not find editable CSM row %s", read_only_newline_course_row.id)
            return FixResult(record_trimmed=0, data_copied=0, record_deleted=0, error=1)

        # Find the StudentModule entry with the correct course_id.
        try:
            correct_course_row = StudentModule.objects.get(
                    student_id=newline_course_row.student_id,
                    course_id=newline_course_row.course_id,
                    module_state_key=newline_course_row.module_state_key,
            )
            assert(correct_course_row.pk != newline_course_row.pk)
        except StudentModule.DoesNotExist:
            correct_course_row = None

        # If only an entry with the newline course exists, just change the
        # course_id and save.
        if correct_course_row is None:
            log.info(
                "No conflict: Removing trailing newline from course_id in CSM row %s - (%s) %s",
                newline_course_row.id,
                newline_course_row.module_type,
                newline_course_row.module_state_key,
            )
            if dry_run:
                return FixResult(record_trimmed=1, data_copied=0, record_deleted=0, error=0)

            try:
                newline_course_row.save()
                return FixResult(record_trimmed=1, data_copied=0, record_deleted=0, error=0)
            except DatabaseError:
                log.exception(
                    "Could not remove newline and update CSM row %s", newline_course_row.id
                )
                return FixResult(record_trimmed=0, data_copied=0, record_deleted=0, error=1)

        # If we're here, then both versions of the row exist. We're going to
        # pick a winner.
        row_to_keep = self.row_to_keep(correct_course_row, newline_course_row)

        # To minimize the chances of conflicts and preserve history, if we
        # decided to keep the data from the newline_course_row, we copy that
        # data into correct_course_row, and then delete the newline_course_row
        # as a cleanup step.
        if row_to_keep.id == newline_course_row.id:
            log.info(
                "Conflict: Choosing data from newline course, copying data from CSM %s to %s - (%s) %s",
                newline_course_row.id,
                correct_course_row.id,
                newline_course_row.module_type,
                newline_course_row.module_state_key
            )
            correct_course_row.grade = newline_course_row.grade
            correct_course_row.max_grade = newline_course_row.max_grade
            correct_course_row.state = newline_course_row.state
            correct_course_row.done = newline_course_row.done

            if dry_run:
                return FixResult(record_trimmed=0, data_copied=1, record_deleted=1, error=0)

            try:
                correct_course_row.save()
                self.delete_row(newline_course_row)
                return FixResult(record_trimmed=0, data_copied=1, record_deleted=1, error=0)
            except DatabaseError:
                log.exception(
                    "Failed while trying save CSM row %s and delete row %s",
                    correct_course_row.id,
                    newline_course_row.id
                )
                return FixResult(record_trimmed=0, data_copied=0, record_deleted=0, error=1)

        # At this point, we either want to keep the data that was originally in
        # correct_course_row or we've already copied the data over from
        # newline_course_row into correct_course_row. Either way, we want to
        # clean things up and delete the newline_course_row entry.
        log.info(
            "Conflict: Choosing data from record with correct course_id (%s) " \
            "and deleting row with newline in course_id (%s) - (%s) %s",
            correct_course_row.id,
            newline_course_row.id,
            newline_course_row.module_type,
            newline_course_row.module_state_key
        )

        if dry_run:
            return FixResult(record_trimmed=0, data_copied=0, record_deleted=1, error=0)

        try:
            self.delete_row(newline_course_row)
            return FixResult(record_trimmed=0, data_copied=0, record_deleted=1, error=0)
        except DatabaseError:
            log.exception("Could not delete CSM row %s", newline_course_row.id)
            return FixResult(record_trimmed=0, data_copied=0, record_deleted=0, error=1)

    def delete_row(self, model):
        """Delete the row and history references to it."""
        for history_row in BaseStudentModuleHistory.get_history([model]):
            history_row.delete()
        model.delete()

    def row_to_keep(self, correct_course_row, newline_course_row):
        """Determine which row's data we want to keep."""
        # Rule 1: Take the higher grade.
        if newline_course_row.grade > correct_course_row.grade:
            return newline_course_row
        elif correct_course_row.grade > newline_course_row.grade:
            return correct_course_row

        # Rule 2: Take the newline course record if they've interacted with it
        #         more recently.
        if newline_course_row.modified > correct_course_row.modified:
            return newline_course_row

        # In all other cases, take the correct_course_row
        return correct_course_row
