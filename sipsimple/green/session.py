from __future__ import with_statement
from contextlib import contextmanager
from application.notification import NotificationCenter
from eventlet import coros
from sipsimple.session import Session
from sipsimple.green import GreenBase
from sipsimple.green.notification import CallFromThreadObserver

class SessionError(Exception):
    pass

class CannotDeliverError(Exception):

    def __init__(self, code, reason, message_id):
        self.code = code
        self.reason = reason
        self.message_id = message_id

    def __str__(self):
        return 'Failed to deliver Mesage-ID=%s: %s %s' % (self.message_id, self.code, self.reason)

class GreenSession(GreenBase):
    klass = Session

    def connect(self, *args, **kwargs):
        """Call connect() on the proxied object. Wait for the session to start or to fail.
        In addition to SCSessionDidStart, wait for MSRPChatDidFail notification.

        In case of an error raise SessionError.
        """
        event_names = ['SCSessionDidStart',
                       'SCSessionDidFail',
                       'SCSessionDidEnd']
        with self.linked_notifications(event_names) as q:
            self._obj.connect(*args, **kwargs)
            while True:
                notification = q.wait()
                if notification.name == 'SCSessionDidStart':
                    break
                else:
                    self._raise_if_error(notification)
        # XXX I would expect Session instance not to fire SCSessionDidStart until all the transports started
        # XXX otherwise I have to go into session and manually wait for different types of events
        # XXX the same goes for terminating - wait for MSRPChatDidEnd before firing SCSessionDidEnd
        self._wait_for_chat_to_start()

    def _raise_if_error(self, notification):
        if notification.name == 'SCSessionDidFail':
            raise SessionError(notification.data.reason)
        if notification.name == 'SCSessionDidEnd':
            if notification.data.originator == "local":
               raise SessionError("Session ended by local party")
            else:
                raise SessionError("Session ended by remote party")
        if notification.name == 'MSRPChatDidFail':
            raise SessionError('Failed to establish MSRP connection')

    def _wait_for_chat_to_start(self):
        if self.chat_transport is not None:
            with self.linked_notifications(['MSRPChatDidStart', 'MSRPChatDidFail'], sender=self.chat_transport) as q:
                with self.linked_notifications(['SCSessionDidFail', 'SCSessionDidEnd'], queue=q):
                    if not self.chat_transport.is_started:
                        notification = q.wait()
                        self._raise_if_error(notification)

    def accept(self, *args, **kwargs):
        """Call accept() on the proxied object. Wait for the session to start or to fail.
        In addition to SCSessionDidStart, wait for MSRPChatDidFail notification.

        In case of an error raise SessionError.
        """
        event_names = ['SCSessionDidStart',
                       'SCSessionDidFail',
                       'SCSessionDidEnd']
        with self.linked_notifications(event_names) as q:
            self._obj.accept(*args, **kwargs)
            while True:
                notification = q.wait()
                if notification.name == 'SCSessionDidStart':
                    break
                else:
                    self._raise_if_error(notification)
        self._wait_for_chat_to_start()

    def terminate(self, *args, **kwargs):
        """Call terminate() on the proxied object. Wait for the session to end.

        In case of an error raise SessionError.
        """
        if self.state in ["NULL", "TERMINATED"]:
            return
        with self.linked_notifications(['SCSessionDidFail', 'SCSessionDidEnd']) as q:
            if self.state != 'TERMINATING':
                self._obj.terminate(*args, **kwargs)
                while True:
                    notification = q.wait()
                    if notification.name == 'SCSessionDidFail':
                        raise SessionError(notification.data.reason)
                    elif notification.name == 'SCSessionDidEnd':
                        return notification

    def deliver_message(self, *args, **kwargs):
        """Call send_message() on the proxied object then wait for
        MSRPChatDidDeliverMessage/MSRPChatDidNotDeliverMessage notification.
        Raise CannotDeliverError if it's the latter.
        """
        events = ['MSRPChatDidDeliverMessage', 'MSRPChatDidNotDeliverMessage', 'MSRPChatDidFail']
        with self.linked_notifications(events, sender=self.chat_transport) as q:
            message = self.send_message(*args, **kwargs)
            while True:
                n = q.wait()
                if n.data.message_id == message.message_id:
                    if n.name == 'MSRPChatDidDeliverMessage':
                        return n.data
                    elif n.name == 'MSRPChatDidNotDeliverMessage':
                        raise CannotDeliverError(code=n.data.code, reason=n.data.reason, message_id=n.data.message_id)
                    else:
                        raise CannotDeliverError('MSRP connection was closed')


@contextmanager
def linked_incoming(queue=None):
    if queue is None:
        queue = coros.queue()
    observer = CallFromThreadObserver(queue.send)
    NotificationCenter().add_observer(observer, 'SCSessionNewIncoming')
    try:
        yield queue
    finally:
        NotificationCenter.remove_observer(observer, 'SCSessionNewIncoming')


