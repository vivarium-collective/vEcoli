import json
import os
import socket
import threading
from uuid import uuid4

"""
The key-values for this are set at the end of each method handler
definition for the message types. See nats.io for the protocol spec.
"""
_op_handlers = {}

class NatsClient(object):
    def __init__(self, *args, client_name=None):
        self.subscriptions = {}
        self.next_sid = 1
        self.lock = threading.Lock()
        self.server_info = {}
        self.response_inbox = 0

        if client_name is not None:
            self.client_name = client_name
        else:
            self.client_name = str(uuid4())

    def __del__(self):
        self.s.close()

    def _process_messages(self):
        while True:
            msg = _next_message(self)
            if msg['op'].upper() == 'MSG':
                if msg['sid'] in self.subscriptions:
                    self.subscriptions[msg['sid']](msg)
            elif msg['op'].upper() == 'PING':
                self._send_pong()
            elif msg['op'].upper() == 'PONG':
                self._send_ping()
            elif msg['op'].upper() == 'INFO':
                self.server_info = msg['info']

    def _send_pong(self):
        self.lock.acquire()
        self.s.send(b'PONG\r\n')
        self.lock.release()

    def _send_ping(self):
        self.lock.acquire()
        self.s.send(b'PING\r\n')
        self.lock.release()

    def connect(self, server=None):
        if server is None:
            server = os.environ['NATS_SERVER']

        server = server.replace("nats://", "")
        host, port = server.split(":")
        port = int(port)

        self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.s.connect((host, port))

        connect_params = {'verbose': False,
                          'pedantic': True,
                          'ssl_required': False,
                          'name': self.client_name,
                          'lang': 'en-us',
                          'version': '0.1'}
        message = 'CONNECT {}\r\n'.format(json.dumps(connect_params)).encode('utf8')
        self.s.send(message)

        self.thread = threading.Thread(target=self._process_messages)
        self.thread.daemon = True
        self.thread.start()


    def request(self, subject, payload, *args, cb=None):
        if cb is None:
            return

        # Generate reply_to inbox
        self.lock.acquire()
        reply_to = "requests.{}.{}".format(self.client_name, self.response_inbox)
        self.response_inbox += 1
        self.lock.release()

        # Subscribe to reply inbox
        sid = self.subscribe(reply_to, cb=cb)
        self.unsubscribe(sid, 1)

        # Send message request
        self.lock.acquire()
        message = 'PUB {} {} {}\r\n'.format(subject, reply_to, len(payload)).encode('utf8')
        message += payload
        message += '\r\n'.encode('utf8')
        self.s.send(message)
        self.lock.release()


    def publish(self, subject, payload):
        self.lock.acquire()
        message = 'PUB {} {}\r\n'.format(subject, len(payload)).encode('utf8')
        message += payload
        message += '\r\n'.encode('utf8')

        self.s.send(message)
        self.lock.release()

    def subscribe(self, subject, queue_group=None, *args, cb=None):
        if cb is None:
            return

        self.lock.acquire()
        sid = self.next_sid
        self.next_sid += 1

        message = 'SUB {} '.format(subject).encode('utf8')
        if queue_group is not None:
            message += '{} '.format(queue_group).encode('utf8')
        message += '{}\r\n'.format(sid).encode('utf8')
        self.subscriptions[sid] = cb

        self.s.send(message)
        self.lock.release()

        return sid

    def unsubscribe(self, sid, max_messages=None):
        self.lock.acquire()
        message = "UNSUB {}".format(sid).encode('utf8')

        if max_messages is not None:
            message += " {}".format(max_messages).encode('utf8')

        self.s.send(message + '\r\n'.encode('utf8'))
        self.lock.release()


def _is_json(val):
    try:
        json.loads(val)
    except:
        return False

    return True


def _read_word(nc):
    word = ''
    last_char = ''

    while last_char != ' ' and last_char != '\r':
        word += last_char
        next_char = nc.s.recv(1)
        last_char = next_char.decode('utf8')

    return word.replace('\n', '').replace('\r', '').replace(' ', '')


def _read_quote(nc):
    opening_char = ''.join(nc.s.recv(1))
    payload = ''
    last_char = ''

    while last_char != opening_char:
        payload += last_char
        last_char = nc.s.recv(1).decode('utf8')

    return opening_char + payload + opening_char


def _read_json(nc):
    """
    This is easily the laziest piece of code in this library. In the future, I want
    to modify this to be a simple CFG parser to identify valid JSON, but in the meantime,
    performance wasn't my priority, and this gets the job done.
    """
    json_str = ''
    last_char = ''

    while not _is_json(json_str):
        json_str += last_char
        last_char = nc.s.recv(1).decode('utf8')

    return json_str


def _next_message(nc):
    op = _read_word(nc)
    return {'op': op,
            **_op_handlers[op.upper()](nc)}


def _handle_info(nc):
    result = json.loads(_read_json(nc))
    nc.s.recv(2)
    return {'info': result}
_op_handlers['INFO'] = _handle_info


def _handle_msg(nc):
    subject = _read_word(nc)
    sid = int(_read_word(nc))

    reply_to = _read_word(nc)
    if reply_to.isnumeric():
        num_bytes = int(reply_to)
        reply_to = None
    else:
        num_bytes = int(_read_word(nc))

    nc.s.recv(1)

    payload = []
    while sum(map(lambda x: len(x), payload)) < num_bytes:
        payload.append(nc.s.recv(num_bytes - sum(map(lambda x: len(x), payload))))

    payload = b''.join(payload)

    nc.s.recv(2)

    return {'subject': subject,
            'sid': sid,
            'reply_to': reply_to,
            'payload': payload}
_op_handlers['MSG'] = _handle_msg


def _handle_ping(nc):
    nc.s.recv(1)
    return {}
_op_handlers['PING'] = _handle_ping


def _handle_pong(nc):
    nc.s.recv(1)
    return {}
_op_handlers['PONG'] = _handle_pong


def _handle_ok(nc):
    nc.s.recv(1)
    return {}
_op_handlers['+OK'] = _handle_ok


def _handle_err(nc):
    error = _read_quote(nc)
    nc.s.recv(2)
    return {'error': error[1:-1]}
_op_handlers['-ERR'] = _handle_err
