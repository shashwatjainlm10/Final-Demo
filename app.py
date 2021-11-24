from flask import Flask, render_template, Response, request, session, Response, jsonify, make_response, json
from flask_socketio import SocketIO, emit, disconnect
from engineio.payload import Payload
from io import StringIO
import io
import base64
from PIL import Image
import cv2
import numpy as np
import imutils
from flask_cors import cross_origin  # flask-cors
from proctoring.ProctoringEngine import Proctor
import time
import atexit
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from threading import Thread
import sqlite3
import sys
import cv2
import os
import time
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas
from matplotlib.figure import Figure

app = Flask(__name__)

app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['CORS_HEADERS'] = 'Content-Type'
app.secret_key = 'super secret key'
app.config['SESSION_TYPE'] = 'filesystem'

proctor = Proctor()
scheduler = BackgroundScheduler()
scheduler.start()

stop_thread = False

@app.route('/')
@cross_origin()
def home():
    return render_template('index.html')

@app.route('/cheat',methods=['POST'])
@cross_origin()
def cheat():
    global msg_q
    print('cheat called')
    if proctor.STATE == 'TEST_INPROCESS':
        proctor.TAB_CHANGE = True
        msg_q.append('Cheating Detected!!')

    return Response(status=201)


@app.route('/login', methods=['POST'])
@cross_origin()
def login():
    enrollment = request.json['enrollment']
    testId = request.json['testId']
    with sqlite3.connect("student.db") as con:
        cur = con.cursor()
        statement = f"SELECT enrollment_number from Student WHERE enrollment_number='{enrollment}' AND Password = '{testId}';"
        cur.execute(statement)
        if not cur.fetchone():  # An empty result evaluates to False.
            print("Login failed")
            return jsonify({'error': 'Wrong Credentials'}), 401  # unauthorize
        else:
            session['enrollment'] = enrollment
            return Response(), 201  # success
    return Response(), 400  # server error


flag = False
frame_q = []
msg_q = []
frame = None


def proctor_task(camera):
    global msg_q, proctor, stop_thread, start_proctoring, frame, scheduler
    t = 0
    warn_for_cheat = 0 # maximum 3 warnings will be given
    while proctor.STATE != 'TERMINATE':
        if proctor.cheat_counter >= 15:
            proctor.STATE = 'TEST_DONE'

        if proctor.STATE == 'TEST_INPROCESS' and proctor.cheat_counter - 5*warn_for_cheat >= 5:
            warn_for_cheat += 1
            msg_q.append("Cheating Detected!!")
        # time.sleep(0.16)
        if frame is None:
            continue
        try:
            if proctor.STATE == 'START_TEST':
                proctor.STATE = 'TEST_INPROCESS'
                proctor.reset_plot_values()  # to clear the values accumulated while calibrations
                scheduler.add_job(func=set_start_test, trigger='date',
                                  run_date=datetime.now()+timedelta(minutes=15), args=[])
                # t = time.time()
                proctor.predict(frame)

            if proctor.STATE == 'TEST_INPROCESS':
                proctor.predict(frame)
        except Exception as e:
            msg_q.append('Cheating Detected!!')
            proctor.STATE == 'TERMINATE'

        if proctor.STATE == 'TEST_DONE':
            msg_q.append('Test Done!!')
            proctor.save_graph()
            proctor.STATE == 'TERMINATE'
            print('Test Done!!')
            return


def calibration_task(camera):
    global msg_q, stop_thread, start_proctoring, frame, proctor, scheduler
    t = 0
    msg_q = []
    while True:
        # time.sleep(0.16)
        if frame is None:
            # get camera frame
            # frame = camera.get_frame()
            continue

        print(proctor.STATE)
        if proctor.STATE == 'CHECK_ROOM_INTENSITY':
            print('checking room intensity')
            # check is sitting in proper lighting or not
            if proctor.check_for_room_light_intensity(frame) == True:
                print('frame intensity good')
                proctor.STATE = 'MEASURE_LIP_DIST'
                msg_q.append('Room Light Fine!!')

            else:
                print('frame intensity bad')
                msg_q.append('Improve Room Light!!')
                start_proctoring = False
                return

        # calibrate user lip distance
        if proctor.STATE == 'MEASURE_LIP_DIST':
            print('measuring lip distance')
            proctor.calibrate_user_lip_distance(frame)
            proctor.STATE = 'CALIBRATE_USER_ORIENT'
        try:
            # caliberating user orientaion
            if proctor.STATE == 'CALIBRATE_USER_ORIENT':
                scheduler.add_job(func=set_user_orient_calibration_done, trigger='date',
                                  run_date=datetime.now()+timedelta(seconds=10), args=[])
                t = time.time()
                app.config['SCHEDULAR_STARTED'] = True
                proctor.calibrating_user_orientation(frame)
                proctor.STATE = 'CALIBRATE_USER_ORIENT_INPROCESS'

            if proctor.STATE == 'CALIBRATE_USER_ORIENT_INPROCESS':
                proctor.calibrating_user_orientation(frame)

            if proctor.STATE == 'CALIBRATE_USER_ORIENT_DONE':
                if proctor.check_calibrated_user_orient() == True:
                    proctor.STATE = 'START_TEST'
                    msg_q.append('All Is Fine!! Good to Go!')
                    start_proctoring = False
                    return
                    # emit('msg', {'error':False,'msg':'All Is Fine!! Good to Go!'})
                else:
                    proctor.STATE = 'CALIBRATE_USER_ORIENT'
                    msg_q.append(
                        'Calibration Can\'t be done properly, sit straight next time')
                    # emit('msg', {'error':True,'msg':'Calibration Can\'t be done properly, sit straight next time'})
                    print('Calibration Can\'t be done properly, sit straight next time')
                    start_proctoring = False
                    return
                    # disconnect() # ends socket con
        except Exception as e:
            proctor.STATE = 'CALIBRATE_USER_ORIENT'
            msg_q.append(
                'Calibration Can\'t be done properly, sit straight next time')
            start_proctoring = False
            return

    return


start_calibration = False
start_proctoring = False


def stream(camera):
    global frame, start_proctoring, msg_q
    while True:
        # get camera frame
        # time.sleep(0.03) #30fps

        flag, frame = camera.get_frame()
        if not flag:
            continue
        ret, jpeg = cv2.imencode('.jpg', frame)
        stream_frame = jpeg.tobytes()
        yield (b'--frame\r\n'
               b'Content-Type: image/jpeg\r\n\r\n' + stream_frame + b'\r\n\r\n')


# t1 = None


@app.route('/calibration_route')
@cross_origin()
def calibration_route():
    global frame, stop_thread, start_proctoring, t1
    return Response(stream(proctor),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/start_cal')
@cross_origin()
def start_cal():
    t1 = Thread(target=calibration_task, args=(proctor,), daemon=True)
    t1.start()
    return Response(status=201)

def get_msg():
    global msg_q
    while True:
        if len(msg_q) != 0:
            m = msg_q.pop(0)
            # yield json.dumps(m)
            yield json.dumps(m)
        else:
            yield json.dumps('')
    return

@app.route('/getCheatCount')
@cross_origin()
def getCheatCount():
    return Response(json.dumps({'count':proctor.cheat_counter}),status=201)

@app.route('/msg')
@cross_origin()
def msg():
    return Response(next(get_msg()))
    # return Response(get_msg(),mimetype='application/json',status=201)

t2 = None


@app.route('/proctor_route')
@cross_origin()
def proctor_route():
    global frame, stop_thread, start_proctoring, t2
    # start the capture thread: reads frames from the camera (non-stop) and stores the result in img
    # a deamon thread is killed when the application exits
    if t2 == None:
        t2 = Thread(target=proctor_task, args=(proctor,), daemon=True)
        t2.start()

    start_proctoring = True
    return Response(stream(proctor), status=201,
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/stop_test')
@cross_origin()
def stop_test():
    proctor.STATE = 'TEST_DONE'
    return Response(status=201)

@app.route('/plot.png')
def plot_png():
    fig = create_figure()
    output = io.BytesIO()
    FigureCanvas(fig).print_png(output)
    return Response(output.getvalue(), mimetype='image/png')

def create_figure():
    fig = Figure()
    axis = fig.add_subplot(1, 1, 1)
    xvals = np.loadtxt('xvals.txt',dtype=float)
    yvals = np.loadtxt('yvals.txt',dtype=float)
    axis.plot(xvals, yvals)
    axis.axhline(y=0.4, color='r', linestyle='-')
    axis.axhline(y=-0.4, color='r', linestyle='-')
    return fig

def set_user_orient_calibration_done():
    print('inside thread1: ', datetime.now())
    proctor.STATE = 'CALIBRATE_USER_ORIENT_DONE'
    # scheduler.shutdown()


def set_start_test():
    print('inside thread2: ', datetime.now())
    proctor.STATE = 'TEST_DONE'
    # scheduler.shutdown()


if __name__ == "__main__":
    # con = sqlite3.connect("student.db")
    # print("Database opened successfully")
    # con.execute("DROP TABLE Student")
    # con.execute("DROP TABLE Questions")
    # con.execute("DROP TABLE Result")
    # con.execute("create table Student (enrollment_number VARCHAR[12] PRIMARY KEY , first_name TEXT NOT NULL, last_name TEXT NOT NULL, password VARCHAR[30] NOT NULL)")
    # con.execute("create table Questions (question_id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, a TEXT NOT NULL, b TEXT NOT NULL, c TEXT NOT NULL, d TEXT NOT NULL, answer TEXT NOT NULL)")
    # con.execute("create table Result (enrollment_number VARCHAR[12] PRIMARY KEY , marks INTEGER NOT NULL) ")
    # print("Table created successfully")

    # cur = con.cursor()
    # cur.execute("INSERT OR IGNORE into Student (enrollment_number,first_name, last_name, password) values (?,?,?,?)",('cs171088','first_name','last_name','abcd'))
    # cur.execute("INSERT OR IGNORE into Student (enrollment_number,first_name, last_name, password) values (?,?,?,?)",('cs171073','first_name','last_name','abcd'))
    # cur.execute("INSERT OR IGNORE into Student (enrollment_number,first_name, last_name, password) values (?,?,?,?)",('cs171069','first_name','last_name','abcd'))
    # cur.execute("INSERT OR IGNORE into Student (enrollment_number,first_name, last_name, password) values (?,?,?,?)",('cs171038','first_name','last_name','abcd'))
    # cur.execute("INSERT OR IGNORE into Student (enrollment_number,first_name, last_name, password) values (?,?,?,?)",('cs171061','first_name','last_name','abcd'))
    # con.commit()
    # msg = "Employee successfully Added"

    # con.close()
    app.run(debug=True)
