import sys
from cli import main

if __name__ == "__main__":
    # Line-buffer stdout even when it is a file, so a job log shows each line as it happens.
    sys.stdout.reconfigure(line_buffering=True)
    main()
