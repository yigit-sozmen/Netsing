import time
import queue
import threading
import warnings
import numpy as np
import sounddevice as sd
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
import scapy.all as scapy
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, DataTable

packet_queue = queue.Queue(maxsize=100)
SAMPLE_RATE = 44100

latest_waveform_amplitude = 0.0



class RadioDSP:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase_mark = 0.0
        self.phase_space = 0.0

        self.burst_active = False
        self.burst_samples_remaining = 0
        self.burst_total_samples = 1

        self.mark_freq = 500.0
        self.space_freq = 1000.0
        self.lp_state = 0.0


dsp = RadioDSP()


def audio_callback(outdata, frames, time_info, status):
    global latest_waveform_amplitude

    output = np.zeros(frames, dtype=np.float32)
    t_chunk = np.arange(frames) / SAMPLE_RATE

    with dsp.lock:
        if not packet_queue.empty():
            latest_pkt = None
            while not packet_queue.empty():
                try:
                    latest_pkt = packet_queue.get_nowait()
                    packet_queue.task_done()
                except queue.Empty:
                    break

            if latest_pkt:
                size = latest_pkt['size']
                protocol = latest_pkt['protocol']

                base_f = 400.0 + min(size // 3, 500)
                dsp.mark_freq = base_f
                dsp.space_freq = base_f * (1.5 if protocol == "UDP" else 1.25)

                duration = 0.08 + min(size / 4000.0, 0.10)
                dsp.burst_total_samples = int(duration * SAMPLE_RATE)
                dsp.burst_samples_remaining = dsp.burst_total_samples
                dsp.burst_active = True

        if dsp.burst_active:
            phase_step_m = 2 * np.pi * dsp.mark_freq / SAMPLE_RATE
            phase_step_s = 2 * np.pi * dsp.space_freq / SAMPLE_RATE

            phases_m = dsp.phase_mark + np.cumsum(np.full(frames, phase_step_m))
            phases_s = dsp.phase_space + np.cumsum(np.full(frames, phase_step_s))

            dsp.phase_mark = phases_m[-1] % (2 * np.pi)
            dsp.phase_space = phases_s[-1] % (2 * np.pi)

            wave_m = np.sin(phases_m)
            wave_s = 0.5 * np.sin(phases_s)

            mod_lfo = 0.5 + 0.5 * np.sin(2 * np.pi * 16.0 * t_chunk)
            raw_tone = (wave_m * mod_lfo) + (wave_s * (1.0 - mod_lfo))

            progress_start = 1.0 - (dsp.burst_samples_remaining / dsp.burst_total_samples)
            progress_end = 1.0 - (max(0, dsp.burst_samples_remaining - frames) / dsp.burst_total_samples)
            progress = np.linspace(progress_start, progress_end, frames)

            env = np.sin(np.pi * np.clip(progress, 0.0, 1.0)) ** 1.5

            sub_body = 0.3 * np.sin(phases_m * 0.5)

            combined = (raw_tone + sub_body) * env * 0.22

            alpha = 0.30
            val = dsp.lp_state
            for i in range(frames):
                val = val + alpha * (combined[i] - val)
                output[i] = val
            dsp.lp_state = val

            output = np.tanh(output * 2.0)

            dsp.burst_samples_remaining -= frames
            if dsp.burst_samples_remaining <= 0:
                dsp.burst_active = False

    latest_waveform_amplitude = float(np.max(np.abs(output))) * 4.0
    outdata[:, 0] = output.astype(np.float32)


def audio_worker():
    stream = sd.OutputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        callback=audio_callback,
        blocksize=1024
    )
    with stream:
        while True:
            time.sleep(0.1)



ui_queue = queue.Queue(maxsize=200)


def process_packet(packet):
    # Only process IP packets (filters out raw 0.0.0.0 noise)
    if not packet.haslayer(scapy.IP):
        return

    src_ip = packet[scapy.IP].src
    dst_ip = packet[scapy.IP].dst
    proto_num = packet[scapy.IP].proto

    protocol = "OTHER"
    if proto_num == 6:
        protocol = "TCP"
    elif proto_num == 17:
        protocol = "UDP"
    elif proto_num == 1:
        protocol = "ICMP"

    pkt_info = {
        'size': len(packet),
        'protocol': protocol,
        'src': src_ip,
        'dst': dst_ip,
        'time': time.strftime("%H:%M:%S")
    }

    try:
        packet_queue.put_nowait(pkt_info)
    except queue.Full:
        pass

    try:
        ui_queue.put_nowait(pkt_info)
    except queue.Full:
        pass


def start_sniffer():
    scapy.sniff(filter="ip", store=False, prn=process_packet)



class NetsingApp(App):
    CSS = """
    Screen {
        background: #000000;
        color: #00ff00;
    }

    #title-box {
        height: 3;
        content-align: center middle;
        background: #000000;
        color: #ff0000;
        text-style: bold;
        border-bottom: heavy #333333;
    }

    #scope-box {
        height: 6;
        border: heavy #ff0000;
        margin: 1 1;
        padding: 0 1;
        background: #000000;
        content-align: center middle;
    }

    #stats-box {
        height: 3;
        margin: 0 1;
        content-align: left middle;
        color: #00ff00;
        text-style: bold;
    }

    DataTable {
        height: 1fr;
        margin: 1 1;
        border: heavy #333333;
        background: #000000;
        color: #00ff00;
    }

    DataTable > .datatable--header {
        background: #000000;
        color: #ff0000;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #222222;
        color: #00ff00;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.scope_history = [0.0] * 50
        self.total_packets = 0
        self.row_keys = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("NETSING / I WANT TO DIE", id="title-box")
        yield Static("", id="scope-box")
        yield Static(" [STATUS] LISTENING | PACKETS: 0 | QUEUE: 0", id="stats-box")
        yield DataTable(id="packet-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("TIME", "PROTO", "SIZE", "SOURCE", "DESTINATION")

        audio_thread = threading.Thread(target=audio_worker, daemon=True)
        audio_thread.start()

        sniffer_thread = threading.Thread(target=start_sniffer, daemon=True)
        sniffer_thread.start()

        self.set_interval(0.05, self.update_ui)

    def update_ui(self) -> None:
        global latest_waveform_amplitude

        self.scope_history.append(latest_waveform_amplitude)
        self.scope_history.pop(0)

        blocks = [" ", " ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
        wave_str = ""
        for val in self.scope_history:
            idx = min(int(val * (len(blocks) - 1)), len(blocks) - 1)
            wave_str += blocks[idx]

        scope = self.query_one("#scope-box", Static)
        scope.update(f"[bold red]SIGNAL WAVEFORM:[/bold red]\n\n[bright_green]{wave_str}[/bright_green]")

        table = self.query_one(DataTable)
        stats = self.query_one("#stats-box", Static)

        q_size = ui_queue.qsize()
        stats.update(f" [STATUS] RUNNING | TOTAL PACKETS: {self.total_packets} | QUEUE BUFFER: {q_size}")


        while not ui_queue.empty():
            try:
                item = ui_queue.get_nowait()
                self.total_packets += 1

                proto_tag = item['protocol']
                if proto_tag == "TCP":
                    proto_fmt = f"[bold green]{proto_tag}[/bold green]"
                elif proto_tag == "UDP":
                    proto_fmt = f"[bold yellow]{proto_tag}[/bold yellow]"
                else:
                    proto_fmt = f"[bold red]{proto_tag}[/bold red]"

                row_key = table.add_row(
                    item['time'],
                    proto_fmt,
                    f"{item['size']} B",
                    item['src'],
                    item['dst']
                )
                self.row_keys.append(row_key)

                if len(self.row_keys) > 50:
                    oldest_key = self.row_keys.pop(0)
                    table.remove_row(oldest_key)

                ui_queue.task_done()
            except queue.Empty:
                break


if __name__ == "__main__":
    app = NetsingApp()
    app.run()