import json
import sys
from unittest.mock import patch

import minio.error
from django.core.management.base import BaseCommand, CommandError, no_translations
from django.utils.module_loading import import_string
from minio_storage.policy import Policy
from minio_storage.storage import MinioStorage


class Command(BaseCommand):
    help = "verify, list, create and delete minio buckets"

    def add_arguments(self, parser):

        group = parser.add_argument_group("minio")
        group.add_argument(
            "--class",
            type=str,
            default="minio_storage.storage.MinioMediaStorage",
            help="Storage class to modify "
            "(media/static are short names for default classes)",
        )
        group.add_argument(
            "--bucket",
            type=str,
            default=None,
            help="bucket name (will use storage class bucket if not set)",
        )

        commands = parser.add_subparsers(
            dest="command", title="subcommands", description="valid subcommands"
        )

        commands.add_parser(
            "mb", help="make bucket (defaults to storage defined bucket)"
        )

        commands.add_parser("rb", help="remove an empty bucket")

        ls = commands.add_parser("ls", help="list bucket objects or buckets")
        ls.add_argument("--dirs", action="store_true", help="include directories")
        ls.add_argument("--files", action="store_true", help="include files")
        ls.add_argument(
            "-r", "--recursive", action="store_true", help="find files recursive"
        )
        ls.add_argument("-p", "--prefix", type=str, default="", help="path prefix")
        ls.add_argument(
            "--buckets", action="store_true", help="list buckets instead of files"
        )

        policy = commands.add_parser("policy", help="get or set bucket policy")
        policy.add_argument(
            "--set",
            type=str,
            default=None,
            choices=[p.value for p in Policy],
            help="set bucket policy",
        )

        super().add_arguments(parser)

    @no_translations
    def handle(self, *args, **options):
        storage = self.storage(options)
        bucket_name = options["bucket"] or storage.bucket_name
        command = options["command"] or ""
        if command == "mb":
            return self.bucket_create(storage, bucket_name)
        elif command == "rb":
            return self.bucket_delete(storage, bucket_name)
        elif command == "ls":
            if options["buckets"]:
                return self.list_buckets(storage)
            elif options["dirs"] or options["files"]:
                list_dirs = options["dirs"]
                list_files = options["files"]
            else:
                list_dirs = True
                list_files = True
            return self.bucket_list(
                storage,
                bucket_name,
                prefix=options["prefix"],
                list_dirs=list_dirs,
                list_files=list_files,
                recursive=options["recursive"],
            )
        elif command == "policy":
            if options["set"] is not None:
                return self.policy_set(
                    storage, bucket_name, policy=Policy(options["set"])
                )
            return self.policy_get(storage, bucket_name)
        else:
            return self.bucket_exists(storage, bucket_name)

    def storage(self, options):
        class_name = {
            "media": "minio_storage.storage.MinioMediaStorage",
            "static": "minio_storage.storage.MinioStaticStorage",
        }.get(options["class"], options["class"])

        try:
            storage_class = import_string(class_name)
        except ImportError:
            raise CommandError(f"could not find storage class: {class_name}")
        if not issubclass(storage_class, MinioStorage):
            raise CommandError(f"{class_name} is not an sub class of MinioStorage.")

        # TODO: maybe another way
        with patch.object(storage_class, "_init_check", return_value=None):
            storage = storage_class()
            return storage

    def bucket_exists(self, storage, bucket_name):
        exists = storage.client.bucket_exists(bucket_name)
        if not exists:
            raise CommandError(f"bucket {bucket_name} does not exist")

    def list_buckets(self, storage):
        objs = storage.client.list_buckets()
        for o in objs:
            print(f"{o.name}")

    def bucket_list(
        self,
        storage,
        bucket_name: str,
        *,
        prefix: str,
        list_dirs: bool,
        list_files: bool,
        recursive: bool,
        summary: bool = True,
    ):
        try:
            objs = storage.client.list_objects_v2(
                bucket_name, prefix=prefix, recursive=recursive
            )
            n_files = 0
            n_dirs = 0
            for o in objs:
                if o.is_dir:
                    n_dirs += 1
                    if list_dirs:
                        print(f"{o.object_name}")
                else:
                    n_files += 1
                    if list_files:
                        print(f"{o.object_name}")
            if summary:
                print(f"{n_files} files and {n_dirs} directories", file=sys.stderr)
        except minio.error.NoSuchBucket:
            raise CommandError(f"bucket {bucket_name} does not exist")

    def bucket_create(self, storage, bucket_name):
        try:
            storage.client.make_bucket(bucket_name)
            print(f"created bucket: {bucket_name}", file=sys.stderr)
        except minio.error.BucketAlreadyOwnedByYou:
            raise CommandError(f"you have already created {bucket_name}")
        return

    def bucket_delete(self, storage, bucket_name):
        try:
            storage.client.remove_bucket(bucket_name)
        except minio.error.NoSuchBucket:
            raise CommandError(f"bucket {bucket_name} does not exist")
        except minio.error.BucketNotEmpty:
            raise CommandError(f"bucket {bucket_name} is not empty")

    def policy_get(self, storage, bucket_name):
        try:
            policy = storage.client.get_bucket_policy(bucket_name)
            policy = json.loads(policy)
            policy = json.dumps(policy, ensure_ascii=False, indent=2)
            return policy
        except (minio.error.NoSuchBucket, minio.error.NoSuchBucketPolicy) as e:
            raise CommandError(e.message)

    def policy_set(self, storage, bucket_name, policy: Policy):
        try:
            policy = Policy(policy)
            storage.client.set_bucket_policy(bucket_name, policy.bucket(bucket_name))
        except minio.error.NoSuchBucket as e:
            raise CommandError(e.message)