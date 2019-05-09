#!/usr/bin/python

import argparse
import datetime
import logging
import math
import os
import requests
import shutil
import socket
import subprocess
import sys
import time

import raven


def make_file_prefix(base_name):
    hostname = socket.gethostname()
    return '{0}_{1}'.format(hostname, base_name)


def make_file_name(base_name):
    """
    Create a file name based on the hostname, a base_name, and date
        e.g. openedxlite12345_mysql_20140102
    """

    return '{0}_{1}'.format(make_file_prefix(base_name), datetime.datetime.now().
                            strftime("%Y%m%d"))


def upload_to_s3(file_path, bucket, aws_access_key_id, aws_secret_access_key):
    """
    Upload a file to the specified S3 bucket.

        file_path: An absolute path to the file to be uploaded.
        bucket: The name of an S3 bucket.
        aws_access_key_id: An AWS access key.
        aws_secret_access_key: An AWS secret access key.
    """

    from filechunkio import FileChunkIO
    import boto

    logging.info('Uploading backup at "{}" to Amazon S3 bucket "{}"'
                 .format(file_path, bucket))

    conn = boto.connect_s3(aws_access_key_id, aws_secret_access_key)
    bucket = conn.lookup(bucket)
    file_name = os.path.basename(file_path)
    file_size = os.stat(file_path).st_size
    chunk_size = 104857600  # 100 MB
    chunk_count = int(math.ceil(file_size / float(chunk_size)))

    multipart_upload = bucket.initiate_multipart_upload(file_name)
    for i in range(chunk_count):
        offset = chunk_size * i
        bytes_to_read = min(chunk_size, file_size - offset)

        with FileChunkIO(file_path, 'r', offset=offset, bytes=bytes_to_read) as fp:
            logging.info('Upload chunk {}/{}'.format(i + 1, chunk_count))
            multipart_upload.upload_part_from_file(fp, part_num=(i + 1))

    multipart_upload.complete_upload()
    logging.info('Upload successful')


def upload_to_gcloud_storage(file_path, bucket):
    """
    Upload a file to the specified Google Cloud Storage bucket.

    Note that the host machine must be properly configured to use boto with a
    Google Cloud Platform service account. See
    https://cloud.google.com/storage/docs/xml-api/gspythonlibrary.

        file_path: An absolute path to the file to be uploaded.
        bucket: The name of a Google Cloud Storage bucket.
    """

    import boto
    import gcs_oauth2_boto_plugin #is needed to authenticate/upload backups to gc storage

    logging.info('Uploading backup at "{}" to Google Cloud Storage bucket '
                 '"{}"'.format(file_path, bucket))

    file_name = os.path.basename(file_path)
    gcloud_uri = boto.storage_uri(bucket + '/' + file_name, 'gs')
    gcloud_uri.new_key().set_contents_from_filename(file_path)
    logging.info('Upload successful')


class NoBackupsFound(Exception):
    pass


def monitor_gcloud_backups(bucket, service, sentry, pushgateway):
    """Double check the backups in the Google Cloud Storage Bucket

    Finds the most recent backup file and pushes the creation
    timestamp to our monitoring. This gives us something of a "dead
    man's switch" to alert us if the previous day's backups failed
    silently.

    We also raise a Sentry error if there are no backups found or
    if this monitoring process fails.

        bucket: The name of a Google Cloud Storage bucket.
        service: the service name (really only supports 'mongodb' currently)
        sentry: The sentry client
        pushgateway: URL of the pushgateway

    """

    import boto
    import gcs_oauth2_boto_plugin

    logging.info('checking backups in Google Cloud Storage bucket '
                 '"{}"'.format(bucket))

    sentry.extra_context({'bucket': bucket})

    try:
        gcloud_uri = boto.storage_uri(bucket, 'gs')
        keys = gcloud_uri.get_all_keys()
        prefix = make_file_prefix(service)
        backups = [k for k in keys if k.key.startswith(prefix)]
        if len(backups) < 1:
            raise NoBackupsFound("There are no backup files in the bucket")
        backups.sort(key=lambda x: x.last_modified)
        most_recent = backups[-1]

        sentry.extra_context({'most_recent': most_recent})
        last_modified = datetime.datetime.strptime(most_recent.last_modified,
                                                   '%Y-%m-%dT%H:%M:%S.%fZ')
        push_backups_age_metric(pushgateway, socket.gethostname(),
                                float(last_modified.strftime('%s')),
                                backups_type=service)
        logging.info('Monitoring successful')
    except Exception:
        sentry.CaptureException()


def push_backups_age_metric(gateway, instance, value, backups_type="mongodb"):
    """ submits backups timestamp to push gateway service

    labelled with the instance (typically hostname) and type ('mongodb'
     or 'mysql')"""

    headers = {
        'Content-type': 'application/octet-stream'
    }
    requests.post(
        '{}/metrics/job/backups_monitor/instance/{}'.format(gateway, instance),
        data='backups_timestamp{type="%s"} %f\n' % (backups_type, value),
        headers=headers)


def compress_backup(backup_path):
    """
    Compress a backup using tar and gzip.

        backup_path: An absolute path to a file or directory containing a
            database dump.

        returns: The absolute path to the compressed backup file.
    """

    logging.info('Compressing backup at "{}"'.format(backup_path))

    compressed_backup_path = backup_path + '.tar.gz'
    zip_cmd = ['tar', '-zcvf', compressed_backup_path, backup_path]

    ret = subprocess.call(zip_cmd, env={'GZIP': '-9'})
    if ret:  # if non-zero return
        error_msg = 'Error occurred while compressing backup'
        logging.error(error_msg)
        raise Exception(error_msg)

    return compressed_backup_path


def dump_service(service_name, backup_dir, user='', password=''):
    """
    Dump the database contents for a service.

        service_name: The name of the service to dump, either mysql or mongodb.
        backup_dir: The directory where the database is to be dumped.

        returns: The absolute path of the file or directory containing the
            dump.
    """

    commands = {
        'mysql': 'mysqldump -u root --all-databases --single-transaction > {}',
        'mongodb': 'mongodump -o {}',
    }

    if user and password:
        commands['mongodb'] += (' --authenticationDatabase admin -u {} -p {}'
                                .format(user, password))

    cmd_template = commands.get(service_name)
    if cmd_template:
        backup_filename = make_file_name(service_name)
        backup_path = os.path.join(backup_dir, backup_filename)
        cmd = cmd_template.format(backup_path)

        logging.info('Dumping database: `{}`'.format(cmd))
        ret = subprocess.call(cmd, shell=True)
        if ret:  # if non-zero return
            error_msg = 'Error occurred while dumping database'
            logging.error(error_msg)
            raise Exception(error_msg)

        return backup_path
    else:
        error_msg = 'Unknown service {}'.format(service_name)
        logging.error(error_msg)
        raise Exception(error_msg)


def clean_up(backup_path):
    """
    Remove the local database dump and the corresponding tar file if it exists.

        backup_path: An absolute path to a file or directory containing a
            database dump.
    """

    logging.info('Cleaning up "{}"'.format(backup_path))

    backup_tar = backup_path + '.tar.gz'
    if os.path.isfile(backup_tar):
        os.remove(backup_tar)

    try:
        if os.path.isdir(backup_path):
            shutil.rmtree(backup_path)
        elif os.path.isfile(backup_path):
            os.remove(backup_path)
    except OSError:
        logging.exception('Removing files at {} failed!'.format(backup_path))


def restore(service_name, backup_path, uncompress=True, settings=None):
    """
    Restore a database from a backup.

        service_name: The name of the service whose database is to be restored,
            either mysql or mongodb.
        backup_path: The absolute path to a backup.
        uncompress: If True, the backup is assumed to be a gzipped tar and is
            uncompressed before the database restoration.
    """

    if service_name == 'mongodb':
        restore_mongodb(backup_path, uncompress)
    elif service_name == 'mysql':
        restore_mysql(backup_path, uncompress, settings=settings)


def restore_mongodb(backup_path, uncompress=True):
    """
    Restore a MongoDB database from a backup.

        backup_path: The absolute path to a backup.
        uncompress: If True, the backup is assumed to be a gzipped tar and is
            uncompressed before the database restoration.
    """

    logging.info('Restoring MongoDB from "{}"'.format(backup_path))

    if uncompress:
        backup_path = _uncompress(backup_path)

    cmd = 'mongorestore {}'.format(backup_path)
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while restoring MongoDB backup'
        logging.error(error_msg)
        raise Exception(error_msg)

    logging.info('MongoDB successfully restored')


def restore_mysql(backup_path, uncompress=True, settings=None):
    """
    Restore a MySQL database from a backup.

        backup_path: The absolute path to a backup.
        uncompress: If True, the backup is assumed to be a gzipped tar and is
            uncompressed before the database restoration.
    """

    logging.info('Restoring MySQL from "{}"'.format(backup_path))

    if uncompress:
        backup_path = _uncompress(backup_path)

    cmd = 'mysqladmin -f drop edxapp'
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while deleting old mysql database'
        logging.error(error_msg)
        raise Exception(error_msg)

    cmd = 'mysqladmin -f create edxapp'
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while creating new mysql database'
        logging.error(error_msg)
        raise Exception(error_msg)

    cmd = 'mysql -D edxapp < {0}'.format(backup_path)
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while restoring mysql database'
        logging.error(error_msg)
        raise Exception(error_msg)

    cmd = ('source /edx/app/edxapp/edxapp_env && /edx/bin/manage.edxapp '
           'lms migrate --settings={}'.format(settings))
    ret = subprocess.call(cmd, shell=True, executable="/bin/bash")
    if ret:  # if non-zero return
        error_msg = 'Error occurred while running edx migrations'
        logging.error(error_msg)
        raise Exception(error_msg)

    cmd = '/edx/bin/supervisorctl restart edxapp:'
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while restarting edx'
        logging.error(error_msg)
        raise Exception(error_msg)

    logging.info('MySQL successfully restored')


def _uncompress(file_path):
    """
    Uncompress a gzipped tar file. The contents of the compressed file are
    extracted to the directory containing the compressed file.

        file_path: An absolute path to a gzipped tar file.

        returns: The directory containing the contents of the compressed file.
    """

    logging.info('Uncompressing file at "{}"'.format(file_path))

    file_dir = os.path.dirname(file_path)
    cmd = 'tar xzvf {}'.format(file_path)
    ret = subprocess.call(cmd, shell=True)
    if ret:  # if non-zero return
        error_msg = 'Error occurred while uncompressing {}'.format(file_path)
        logging.error(error_msg)
        raise Exception(error_msg)

    return file_path.replace('.tar.gz', '')


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('service', help='mongodb or mysql')
    parser.add_argument('-r', '--restore-path',
                        help='path to a backup used to restore a database')
    parser.add_argument('-d', '--dir', dest='backup_dir',
                        help='temporary storage directory used during backup')
    parser.add_argument('-u', '--user', help='database user')
    parser.add_argument('--password', help='database password')
    parser.add_argument('-p', '--provider', help='gs or s3')
    parser.add_argument('-b', '--bucket', help='bucket name')
    parser.add_argument('-i', '--s3-id', dest='s3_id',
                        help='AWS access key id')
    parser.add_argument('-k', '--s3-key', dest='s3_key',
                        help='AWS secret access key')
    parser.add_argument('-n', '--uncompressed', dest='compressed',
                        action='store_false', default=True,
                        help='disable compression')
    parser.add_argument('-s', '--settings',
                        help='Django settings used when running database '
                             'migrations')
    parser.add_argument('--sentry-dsn', help='Sentry data source name')
    parser.add_argument('--pushgateway', help='Prometheus pushgateway URL')

    return parser.parse_args()


def _main():
    args = _parse_args()

    program_name = os.path.basename(sys.argv[0])
    backup_dir = (args.backup_dir or os.environ.get('BACKUP_DIR',
                                                    '/tmp/db_backups'))
    user = args.user or os.environ.get('BACKUP_USER', '')
    password = args.password or os.environ.get('BACKUP_PASSWORD', '')
    bucket = args.bucket or os.environ.get('BACKUP_BUCKET')
    compressed = args.compressed
    provider = args.provider or os.environ.get('BACKUP_PROVIDER', 'gs')
    restore_path = args.restore_path
    s3_id = args.s3_id or os.environ.get('BACKUP_AWS_ACCESS_KEY_ID')
    s3_key = args.s3_key or os.environ.get('BACKUP_AWS_SECRET_ACCESS_KEY')
    settings = args.settings or os.environ.get('BACKUP_SETTINGS', 'aws_appsembler')
    sentry_dsn = args.sentry_dsn or os.environ.get('BACKUP_SENTRY_DSN', '')
    pushgateway = args.pushgateway or os.environ.get('PUSHGATEWAY', 'https://pushgateway.infra.appsembler.com')
    service = args.service

    sentry = raven.Client(sentry_dsn)

    if program_name == 'edx_backup':
        backup_path = ''

        try:
            if not os.path.exists(backup_dir):
                os.makedirs(backup_dir)
            backup_path = dump_service(service, backup_dir, user, password)

            if compressed:
                backup_path = compress_backup(backup_path)

            if provider == 'gs':
                upload_to_gcloud_storage(backup_path, bucket)
            elif provider == 's3':
                upload_to_s3(backup_path, bucket, aws_access_key_id=s3_id,
                             aws_secret_access_key=s3_key)
            else:
                error_msg = ('Invalid storage provider specified. Please use '
                             '"gs" or "s3".')
                logging.warning(error_msg)
        except:
            logging.exception("The backup failed!")
            sentry.captureException(fingerprint=['{{ default }}', time.time()])
        finally:
            clean_up(backup_path.replace('.tar.gz', ''))

    elif program_name == 'edx_restore':
        restore(service, restore_path, compressed, settings=settings)

    elif program_name == 'edx_backups_monitor':
        if provider == 'gs':
            monitor_gcloud_backups(bucket, service, sentry, pushgateway)
        else:
            # other providers not supported yet
            logging.warning("no backup monitoring available for this provider")


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stdout, level=logging.INFO)
    _main()
