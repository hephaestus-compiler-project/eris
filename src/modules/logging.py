import os
import traceback

from src.utils import mkdir


class Logger():
    def __init__(self, session, test_directory, iteration, name, number,
                 stdout=False, fixed_filename=None, subdir=None):
        self.session = session
        self.test_directory = test_directory
        self.iteration = iteration
        self.transformation_name = name
        self.transformation_number = number
        self.stdout = stdout
        self._fixed = False
        if not self.stdout:
            logs_dir = os.path.join(self.test_directory, "logs")
            if fixed_filename is not None:
                # Single fixed-file mode: all writes go to the same file;
                # update_filename() is a no-op.
                self._fixed = True
                mkdir(logs_dir)
                self.directory = logs_dir
                self.filename = os.path.join(logs_dir, fixed_filename)
            else:
                # Per-program mode: one file per iteration, optionally under a
                # subdirectory of logs/.
                if subdir:
                    self.directory = os.path.join(logs_dir, subdir)
                else:
                    self.directory = logs_dir
                mkdir(self.directory)
                self.filename = os.path.join(self.directory, str(self.iteration))

    def update_filename(self, iteration):
        self.iteration = iteration
        if not self.stdout and not self._fixed:
            self.filename = os.path.join(self.directory, str(self.iteration))

    def log_info(self):
        msg = "\n{}\nTransformation name:{}\nTransformation No: {}\n\n".format(
            10*"=",
            self.transformation_name,
            self.transformation_number
        )
        self.log(msg)

    def log(self, msg):
        if self.stdout:
            print(msg)
        else:
            with open(self.filename, 'a') as out:
                out.write(str(msg))
                out.write('\n')


def log_error(logger, exc):
    if logger is None:
        return
    err = str(traceback.format_exc())
    log(logger, err)


def log_onerror(func):
    def inner(*args, **kwargs):
        try:
            res = func(*args, **kwargs)
            return res
        except Exception as e:
            self = args[0]
            if self.logger is None:
                return None
            log_error(self.logger, e)

    return inner


def log(logger: Logger, msg: str):
    if logger is not None:
        logger.log(msg)
