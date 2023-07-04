#! /bin/bash
kill `ps -ef | grep "python main.py" | grep -v grep | awk '{print $2}' `
source ./venv/bin/activate
#zsh
nohup python main.py &

start() {
    kill `ps -ef | grep "python main.py" | grep -v grep | awk '{print $2}' `
    source ./venv/bin/activate
    nohup python main.py &
}

stop () {
    kill `ps -ef | grep "python main.py" | grep -v grep | awk '{print $2}' `
}

restart () {
    kill `ps -ef | grep "python main.py" | grep -v grep | awk '{print $2}' `
    source ./venv/bin/activate
    nohup python main.py &
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    *)
        echo "Usage: $0 {start|stop|restart}"
esac