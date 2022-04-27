# `moot`

Run a command but only show its output on error.

## Examples

`moot` shows a summary instead of the command's normal output:

```sh
moot "Updating package repository" sudo apt-get update
```

```
Updating package repository 🗸
```

If the command errors, the output will be shown:

```sh
moot "Installing windows-95" sudo apt-get install windows-95
```

```
Installing windows-95 ✗
$ sudo apt-get install windows-95
[0.0] Reading package lists...
[0.0] Building dependency tree...
[0.2] Reading state information...
{0.3} E: Unable to locate package windows-95
> exit: 100
> duration: 0.27s
```

You can run multiple commands by using a here document:

```sh
moot "Update and upgrade" <<'moot'
  sudo apt-get update
  sudo apt-get upgrade
moot
```

```
Update and upgrade 🗸
```

## Install

```sh
pip3 install --user moot
```

`moot` is also just a single file with no dependencies (apart from Python 3) so you can just download it directly:

```sh
wget -O /usr/local/bin/moot \
  https://raw.githubusercontent.com/vjagaro/moot/main/moot.py
chmod a+x /usr/local/bin/moot
```

## Help

```
usage: moot [OPTIONS ...] SUMMARY [COMMAND ...]

Run COMMAND with its output suppressed and SUMMARY shown instead. If
COMMAND errors, then its output will be shown.

optional arguments:
  -h, --help           show this help message and exit
  -l FILE, --log FILE  additionally write output to FILE
  -a, --always-output  show output regardless of error state
  --no-color           suppress color
  --no-info            suppress info (command, exit code, duration)
  --no-timestamps      suppress timestamps
```

## Advanced Techniques

When using `moot` with a here document to run multiple commands, you normally
don't have access to functions and variables in the calling scope. With Bash,
you can use the following helper function to get around this:

```sh
moot() {
  MOOT_SHELL_ENV="$(
    cat <(declare -p | grep -vE '^declare -(r|.r)') <(declare -fp) \
      <(echo "set -eou pipefail")
  )" command moot "$@"
}
```

Then, you can do things like:

```sh
MESSAGE="Hello!"

yell() {
  echo "$@" > "/tmp/message"
}

moot "Greetings" <<'moot'
  yell "$MESSAGE"
  yell "Again, $MESSAGE"
moot
