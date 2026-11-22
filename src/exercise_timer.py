"""ESP32 MicroPython smartwatch integration shell.

Run `python main.py` for a console demo, or copy to the ESP32 as main.py.
The demo uses mock data; no sensor drivers or health algorithms are included.
Replace build_demo() with build_watch() wiring real shared services/features.

Feature contract: init(services, settings), update(now_ms),
handle_event(event), get_view(), get_status().
Service contract: init(services, settings), update(now_ms).
All methods must return promptly. Only the display service draws screens.
Shared acquisition services own sensors; features consume their buffers/state.
"""
import time

try:
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
except AttributeError:  # CPython classroom demo
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(new, old):
        return new - old

    def sleep_ms(milliseconds):
        time.sleep(milliseconds / 1000)


FEATURE_NAMES = (
    'clock', 'alarms', 'heart_rate', 'spo2', 'steps', 'distance',
    'calories', 'activity', 'sleep', 'fall_detection', 'gps', 'sos',
    'weather', 'notifications', 'exercise_timer', 'dashboard',
)


class Feature:
    """Base class for student modules; override the relevant methods."""
    def init(self, services, settings):
        self.services = services
        self.settings = settings

    def update(self, now_ms):
        pass

    def handle_event(self, event):
        pass

    def get_view(self):
        return {}

    def get_status(self):
        return {'ready': True, 'error': None}


class SmartWatch:
    """Single cooperative scheduler and event router for the whole watch.

    Register services before their consumers. init() runs in registration order.
    A failed component is disabled until restart. Other components continue.
    Timing is cooperative: a blocking component can still block the watch.
    """
    def __init__(self, settings=None, event_limit=32, events_per_cycle=8,
                 display_interval_ms=200, clock=None, sleeper=None):
        if event_limit < 1 or events_per_cycle < 1 or display_interval_ms < 1:
            raise ValueError('Queue limits and display interval must be positive')
        self.settings = settings if settings is not None else {}
        self.state = {}
        self.services = {'state': self.state, 'emit': self.emit,
                         'publish': self.publish, 'read': self.read}
        self.tasks = []
        self.features = {}
        self.events = []
        self.event_limit = event_limit
        self.events_per_cycle = events_per_cycle
        self.dropped_events = 0
        self.active_screen = None
        self.running = False
        self.initialized = False
        self._clock = clock or ticks_ms
        self._sleep = sleeper or sleep_ms
        self._display_interval = display_interval_ms
        self._last_display = None
        self.display_error = None

    def _register(self, name, component, interval_ms, kind):
        if self.initialized:
            raise RuntimeError('Register all components before initialize()')
        if interval_ms < 1:
            raise ValueError('Task interval must be positive')
        if name in self.services or name in self.features:
            raise ValueError('Duplicate or reserved name: ' + name)
        task = {'name': name, 'component': component, 'kind': kind,
                'interval': interval_ms, 'last': None, 'enabled': True,
                'error': None, 'max_duration_ms': 0, 'overruns': 0}
        self.tasks.append(task)
        return task

    def register_service(self, name, component, interval_ms=20):
        self._register(name, component, interval_ms, 'service')
        self.services[name] = component

    def register_feature(self, name, component, interval_ms=100):
        task = self._register(name, component, interval_ms, 'feature')
        self.features[name] = task
        if self.active_screen is None:
            self.active_screen = name

    def publish(self, name, value, unit='', valid=True, reason=None):
        """Publish a current measurement, never treating unavailable as zero."""
        self.state[name] = {'value': value if valid else None, 'unit': unit,
                            'timestamp_ms': self._clock(), 'valid': bool(valid),
                            'reason': reason}

    def read(self, name, max_age_ms=None):
        """Return a copy, optionally marking an old measurement unavailable."""
        record = self.state.get(name)
        if record is None:
            return {'value': None, 'valid': False, 'reason': 'unavailable',
                    'unit': '', 'timestamp_ms': None}
        result = dict(record)
        if max_age_ms is not None and ticks_diff(
                self._clock(), result['timestamp_ms']) > max_age_ms:
            result.update(value=None, valid=False, reason='stale')
        return result

    def emit(self, event):
        """Enqueue from ordinary task context (not interrupt handlers).

        Queue overflow rejects the new event and returns False. Alert producers
        must check this result and retain/retry important alerts.
        """
        if not isinstance(event, dict) or 'type' not in event:
            raise ValueError('An event must be a dict containing type')
        if len(self.events) >= self.event_limit:
            self.dropped_events += 1
            return False
        item = dict(event)
        item.setdefault('timestamp_ms', self._clock())
        self.events.append(item)
        return True

    def _fail(self, task, error):
        task['enabled'] = False
        task['error'] = str(error)
        # Consumers must still validate data freshness when a producer fails.
        print('Component disabled:', task['name'], str(error))

    def initialize(self):
        if self.initialized:
            return
        self.initialized = True
        for task in self.tasks:
            try:
                task['component'].init(self.services, self.settings)
            except Exception as error:
                self._fail(task, error)

    def _navigate(self, direction):
        names = [name for name in self.features
                 if self.features[name]['enabled']]
        if not names:
            self.active_screen = None
            return
        index = names.index(self.active_screen) if self.active_screen in names else 0
        self.active_screen = names[(index + direction) % len(names)]

    def _dispatch_events(self):
        # Process only the initial batch; emitted follow-up events wait a cycle.
        count = min(len(self.events), self.events_per_cycle)
        batch = self.events[:count]
        del self.events[:count]
        for event in batch:
            event_type = event['type']
            if event_type == 'NEXT_SCREEN':
                self._navigate(1)
            elif event_type == 'PREVIOUS_SCREEN':
                self._navigate(-1)
            elif event_type == 'OPEN_SCREEN':
                target = event.get('screen')
                if target in self.features and self.features[target]['enabled']:
                    self.active_screen = target
            elif event_type == 'STOP':
                self.stop()
            # Services such as the alert manager may also consume events.
            for task in self.tasks:
                handler = getattr(task['component'], 'handle_event', None)
                if task['enabled'] and handler is not None:
                    try:
                        handler(dict(event))
                    except Exception as error:
                        self._fail(task, error)

    def _render(self):
        display = self.services.get('display')
        if display is None or self.display_error is not None:
            return
        display_tasks = [t for t in self.tasks if t['name'] == 'display']
        if display_tasks and not display_tasks[0]['enabled']:
            return
        task = self.features.get(self.active_screen)
        view = {'message': 'No feature registered'}
        if task is not None:
            if task['enabled']:
                try:
                    view = task['component'].get_view()
                except Exception as error:
                    self._fail(task, error)
                    view = {'message': 'Feature unavailable', 'error': task['error']}
            else:
                view = {'message': 'Feature unavailable', 'error': task['error']}
        try:
            display.render(self.active_screen, view)
        except Exception as error:
            self.display_error = str(error)
            print('Display rendering disabled:', self.display_error)

    def step(self):
        """Execute one cycle; useful for deterministic tests."""
        self.initialize()
        for task in self.tasks:
            now = self._clock()
            if task['enabled'] and (task['last'] is None or ticks_diff(
                    now, task['last']) >= task['interval']):
                task['last'] = now  # Skip missed periods; avoid catch-up bursts.
                start = now
                try:
                    task['component'].update(now)
                except Exception as error:
                    self._fail(task, error)
                duration = ticks_diff(self._clock(), start)
                task['max_duration_ms'] = max(task['max_duration_ms'], duration)
                if duration > task['interval']:
                    task['overruns'] += 1
        self._dispatch_events()
        now = self._clock()
        if self._last_display is None or ticks_diff(
                now, self._last_display) >= self._display_interval:
            self._last_display = now
            self._render()

    def diagnostics(self):
        return {'tasks': {t['name']: {
                'enabled': t['enabled'], 'error': t['error'],
                'max_duration_ms': t['max_duration_ms'],
                'overruns': t['overruns']} for t in self.tasks},
                'queued_events': len(self.events),
                'dropped_events': self.dropped_events,
                'display_error': self.display_error}

    def run(self, duration_ms=None, idle_ms=5):
        """Run indefinitely, or for a bounded classroom demo duration."""
        if idle_ms < 1 or (duration_ms is not None and duration_ms < 0):
            raise ValueError('Invalid run timing')
        start = self._clock()
        self.running = True
        try:
            while self.running:
                if duration_ms is not None and ticks_diff(
                        self._clock(), start) >= duration_ms:
                    break
                self.step()
                self._sleep(idle_ms)
        finally:
            self.running = False
            # Optional shutdown hooks release service/feature resources.
            for task in reversed(self.tasks):
                shutdown = getattr(task['component'], 'shutdown', None)
                if shutdown is not None:
                    try:
                        shutdown()
                    except Exception as error:
                        print('Shutdown error:', task['name'], str(error))

    def stop(self):
        self.running = False


class ConsoleDisplay(Feature):
    """Replace with an OLED service implementing render(screen, view)."""
    def render(self, screen, view):
        print('[MOCK]', screen, view)


class MockSteps(Feature):
    def init(self, services, settings):
        super().init(services, settings)
        self.count = 0

    def update(self, now_ms):
        self.count += 1  # Synthetic demonstration; not step detection.
        self.services['publish']('steps', self.count, 'steps')

    def get_view(self):
        return self.services['read']('steps', 2000)


class Distance(Feature):
    """Example dependent feature: distance per step, not two-step stride."""
    def update(self, now_ms):
        steps = self.services['read']('steps', 2000)
        length = self.settings.get('step_length_m')
        valid = steps['valid'] and isinstance(length, (int, float)) and length > 0
        value = steps['value'] * length if valid else None
        self.services['publish']('distance', value, 'm', valid,
                                 None if valid else 'steps_or_calibration_unavailable')

    def get_view(self):
        return self.services['read']('distance', 2000)


# ==========================================
# NEWLY ADDED EXERCISE TIMER FEATURE CLASS
# ==========================================
class ExerciseTimer(Feature):
    """Exercise monitor supporting stopwatch, countdown, laps, and intervals."""
    
    MODES = ('stopwatch', 'countdown', 'intervals')
    
    def init(self, services, settings):
        super().init(services, settings)
        self.mode = 'stopwatch'
        self.running = False
        self.start_time_ms = 0
        self.elapsed_ms = 0
        self.countdown_target_ms = 60000  # Default 1 minute countdown
        self.laps = []
        
        # Interval state (work/rest cycles)
        self.work_duration_ms = 30000   # 30s work
        self.rest_duration_ms = 10000   # 10s rest
        self.is_work_phase = True
        
    def update(self, now_ms):
        if not self.running:
            return
            
        if self.mode == 'stopwatch':
            current_elapsed = self.elapsed_ms + ticks_diff(now_ms, self.start_time_ms)
            self.services['publish']('exercise_time', current_elapsed, 'ms', True)
            
        elif self.mode == 'countdown':
            remaining = self.countdown_target_ms - (self.elapsed_ms + ticks_diff(now_ms, self.start_time_ms))
            if remaining <= 0:
                remaining = 0
                self.running = False
            self.services['publish']('exercise_time', remaining, 'ms', True)
            
        elif self.mode == 'intervals':
            phase_elapsed = self.elapsed_ms + ticks_diff(now_ms, self.start_time_ms)
            limit = self.work_duration_ms if self.is_work_phase else self.rest_duration_ms
            
            if phase_elapsed >= limit:
                self.is_work_phase = not self.is_work_phase
                self.elapsed_ms = 0
                self.start_time_ms = now_ms
                phase_elapsed = 0
                
            self.services['publish']('exercise_time', limit - phase_elapsed, 'ms', True)
            self.services['publish']('exercise_phase', 'Work' if self.is_work_phase else 'Rest', '', True)

    def handle_event(self, event):
        etype = event.get('type')
        now_ms = ticks_ms()
        
        if etype == 'EXERCISE_TOGGLE':
            if self.running:
                self.elapsed_ms += ticks_diff(now_ms, self.start_time_ms)
                self.running = False
            else:
                self.start_time_ms = now_ms
                self.running = True
                
        elif etype == 'EXERCISE_RESET':
            self.running = False
            self.elapsed_ms = 0
            self.laps = []
            self.is_work_phase = True
            
        elif etype == 'EXERCISE_LAP' and self.running and self.mode == 'stopwatch':
            current_elapsed = self.elapsed_ms + ticks_diff(now_ms, self.start_time_ms)
            self.laps.append(current_elapsed)

    def get_view(self):
        time_record = self.services['read']('exercise_time', 2000)
        phase_record = self.services['read']('exercise_phase', 2000)
        
        return {
            'mode': self.mode,
            'running': self.running,
            'time_ms': time_record['value'] or 0,
            'laps': list(self.laps),
            'phase': phase_record.get('value') if self.mode == 'intervals' else None
        }


def build_demo():
    watch = SmartWatch(settings={'step_length_m': 0.65},
                       display_interval_ms=1000)
    watch.register_service('display', ConsoleDisplay(), 1000)
    watch.register_feature('steps', MockSteps(), 1000)
    watch.register_feature('distance', Distance(), 200)
    
    # Register the exercise timer feature here:
    watch.register_feature('exercise_timer', ExerciseTimer(), 100)
    
    # Optional test events: open the exercise screen and start it
    watch.emit({'type': 'OPEN_SCREEN', 'screen': 'exercise_timer'})
    watch.emit({'type': 'EXERCISE_TOGGLE'})
    return watch


if __name__ == '__main__':
    watch = build_demo()
    try:
        watch.run(duration_ms=5000)
    except KeyboardInterrupt:
        watch.stop()
    print(watch.diagnostics())
