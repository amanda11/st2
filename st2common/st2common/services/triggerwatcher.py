# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import eventlet
from kombu.mixins import ConsumerMixin
from kombu import Connection
from oslo.config import cfg

from st2common import log as logging
from st2common.transport import reactor, publishers

LOG = logging.getLogger(__name__)


class TriggerWatcher(ConsumerMixin):

    TRIGGER_WATCH_Q = reactor.get_trigger_cud_queue('st2.trigger.watch',
                                                    routing_key='#')
    sleep_interval = 4  # how long to sleep after processing each message

    def __init__(self, create_handler, update_handler, delete_handler,
                 trigger_types=None):
        """
        :param create_handler: Function which is called on TriggerDB create event.
        :type create_handler: ``callable``

        :param update_handler: Function which is called on TriggerDB update event.
        :type update_handler: ``callable``

        :param delete_handler: Function which is called on TriggerDB delete event.
        :type delete_handler: ``callable``

        :param trigger_types: If provided, handler function will only be called
                              if the trigger in the message payload is included
                              in this list.
        :type trigger_types: ``list``
        """
        # TODO: Handle trigger type filtering using routing key
        self._create_handler = create_handler
        self._update_handler = update_handler
        self._delete_handler = delete_handler
        self._trigger_types = trigger_types

        self.connection = None
        self._thread = None

        self._handlers = {
            publishers.CREATE_RK: create_handler,
            publishers.UPDATE_RK: update_handler,
            publishers.DELETE_RK: delete_handler
        }

    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=[self.TRIGGER_WATCH_Q],
                         accept=['pickle'],
                         callbacks=[self.process_task])]

    def process_task(self, body, message):
        LOG.debug('process_task')
        LOG.debug('     body: %s', body)
        LOG.debug('     message.properties: %s', message.properties)
        LOG.debug('     message.delivery_info: %s', message.delivery_info)

        routing_key = message.delivery_info.get('routing_key', '')
        handler = self._handlers.get(routing_key, None)

        if not handler:
            LOG.debug('Skipping message %s as no handler was found.', message)
            return

        trigger_type = getattr(body, 'type', None)
        if self._trigger_types and trigger_type not in self._trigger_types:
            LOG.debug('Skipping message %s since\'t trigger_type doesn\'t match (type=%s)',
                      message, trigger_type)
            return

        try:
            handler(body)
        except Exception as e:
            LOG.exception('Handling failed. Message body: %s. Exception: %s',
                          body, e.message)
        finally:
            message.ack()

        eventlet.sleep(self.sleep_interval)

    def start(self):
        try:
            self.connection = Connection(cfg.CONF.messaging.url)
            self._thread = eventlet.spawn(self.run)
        except:
            LOG.exception('Failed to start watcher.')
            self.connection.release()

    def stop(self):
        try:
            self._thread = eventlet.kill(self._thread)
        finally:
            self.connection.release()

    # Note: We sleep after we consume a message so we give a chance to other
    # green threads to run. If we don't do that, ConsumerMixin will block on
    # waiting for a message on the queue.

    def on_consume_end(self, connection, channel):
        super(TriggerWatcher, self).on_consume_end(connection=connection,
                                                   channel=channel)
        eventlet.sleep(seconds=self.sleep_interval)

    def on_iteration(self):
        super(TriggerWatcher, self).on_iteration()
        eventlet.sleep(seconds=self.sleep_interval)
