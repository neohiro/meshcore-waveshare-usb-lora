// Package contract verifies that the modem firmware and meshcore-go, the bot
// that will actually drive it, agree on the wire.
//
// The firmware side is the real src/kiss.c and src/kiss_frame.c, compiled into
// the kiss-server binary with only the USART and SX1262 stubbed. This side is
// meshcore-go's real hardware.KissModem, and framing is decoded with
// meshcore-go's own DecodeFrame. They talk over a pipe, so a mismatch in
// framing, in SetHardware encoding, or in the data/RxMeta pairing shows up here
// rather than as a modem that silently never answers.
package contract

import (
	"bytes"
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"sync"
	"testing"
	"time"

	"github.com/meshcore-go/meshcore-go/hardware"
)

const frameTimeout = 3 * time.Second

// serverPath is supplied by tools/test.ps1, which builds the C binary first.
func serverPath(t *testing.T) string {
	t.Helper()

	p := os.Getenv("KISS_SERVER_BIN")
	if p == "" {
		t.Skip("KISS_SERVER_BIN not set; run through tools/test.ps1")
	}

	if _, err := os.Stat(p); err != nil {
		t.Fatalf("kiss server binary not found at %s: %v", p, err)
	}

	return p
}

func discardLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// pipeTransport speaks to the firmware over the server process' pipes.
type pipeTransport struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	stdout io.ReadCloser

	mu      sync.Mutex
	handler func(*hardware.KissFrame)

	frameCh chan *hardware.KissFrame
	done    chan struct{}
	errCh   chan error
	once    sync.Once
}

func newPipeTransport(t *testing.T, bin string) *pipeTransport {
	t.Helper()

	cmd := exec.Command(bin)
	cmd.Stderr = os.Stderr

	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatalf("stdin pipe: %v", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatalf("stdout pipe: %v", err)
	}

	if err := cmd.Start(); err != nil {
		t.Fatalf("start kiss server: %v", err)
	}

	tr := &pipeTransport{
		cmd:     cmd,
		stdin:   stdin,
		stdout:  stdout,
		frameCh: make(chan *hardware.KissFrame, 64),
		done:    make(chan struct{}),
		errCh:   make(chan error, 1),
	}

	go tr.readLoop()

	t.Cleanup(func() {
		_ = stdin.Close()
		_ = cmd.Wait()
	})

	return tr
}

func (tr *pipeTransport) readLoop() {
	var fr frameFramer

	buf := make([]byte, 4096)

	for {
		n, err := tr.stdout.Read(buf)
		if n > 0 {
			for _, raw := range fr.push(buf[:n]) {
				f, decErr := hardware.DecodeFrame(raw)
				if decErr != nil {
					continue
				}

				// Deliver to the client first, exactly as the real serial
				// transport does, so its state machines advance on the same
				// frames the assertions below look at.
				tr.mu.Lock()
				h := tr.handler
				tr.mu.Unlock()

				if h != nil {
					h(f)
				}

				select {
				case tr.frameCh <- f:
				case <-tr.done:
					return
				}
			}
		}

		if err != nil {
			if !errors.Is(err, io.EOF) {
				select {
				case tr.errCh <- err:
				default:
				}
			}
			return
		}
	}
}

// frameFramer splits a KISS byte stream into whole frames, honouring escapes so
// that an escaped 0xC0 inside a payload is not mistaken for a delimiter. The
// slices it returns are raw frames, delimiters included, which is what
// hardware.DecodeFrame expects.
type frameFramer struct {
	buf   []byte
	esc   bool
	inFrm bool
}

func (f *frameFramer) push(chunk []byte) [][]byte {
	var out [][]byte

	for _, b := range chunk {
		if f.esc {
			f.esc = false
			f.buf = append(f.buf, b)
			continue
		}

		switch {
		case b == 0xDB:
			f.esc = true
			f.inFrm = true
			f.buf = append(f.buf, b)
		case b == 0xC0:
			if f.inFrm {
				f.inFrm = false
				f.buf = append(f.buf, b)

				// Must copy: the buffer is reused for the next frame, so a
				// slice of it would be overwritten behind the reader's back.
				out = append(out, bytes.Clone(f.buf))
				f.buf = f.buf[:0]
			} else {
				// Opening delimiter or padding.
				f.inFrm = true
				f.buf = append(f.buf[:0], b)
			}
		default:
			f.inFrm = true
			f.buf = append(f.buf, b)
		}
	}

	return out
}

func (tr *pipeTransport) Connect(context.Context) error { return nil }

func (tr *pipeTransport) Close() error {
	tr.once.Do(func() { close(tr.done) })
	return nil
}

func (tr *pipeTransport) Send(data []byte) error {
	_, err := tr.stdin.Write(data)
	return err
}

func (tr *pipeTransport) SetFrameHandler(h func(*hardware.KissFrame)) {
	tr.mu.Lock()
	tr.handler = h
	tr.mu.Unlock()
}

func (tr *pipeTransport) SetErrorHandler(func(error)) {}

func (tr *pipeTransport) Dead() <-chan struct{} { return tr.done }

func (tr *pipeTransport) expectFrame(t *testing.T) *hardware.KissFrame {
	t.Helper()

	select {
	case f := <-tr.frameCh:
		return f
	case err := <-tr.errCh:
		t.Fatalf("transport error: %v", err)
	case <-time.After(frameTimeout):
		t.Fatalf("no frame from the modem within %s", frameTimeout)
	}

	// Unreachable: every case above either returns or aborts the test.
	return nil
}

// control sends a harness-only frame using type 0xFE, which is not a KISS
// command, so it cannot collide with real host traffic. The firmware's parser
// hands it to the server, which applies it to the stubs instead.
func (tr *pipeTransport) control(t *testing.T, payload string) {
	t.Helper()

	var wire []byte
	wire = append(wire, 0xC0, 0xFE)
	for i := 0; i < len(payload); i++ {
		b := payload[i]
		if b == 0xC0 || b == 0xDB {
			wire = append(wire, 0xDB, b^0x20)
		} else {
			wire = append(wire, b)
		}
	}
	wire = append(wire, 0xC0)

	if err := tr.Send(wire); err != nil {
		t.Fatalf("send control: %v", err)
	}
}

// newModem builds a client against the firmware under test and consumes the
// SET_SIGNAL_REPORT reply that Connect sends, so callers start from a known
// frame position.
func newModem(t *testing.T, tr *pipeTransport, opts ...hardware.ModemOption) *hardware.KissModem {
	t.Helper()

	base := []hardware.ModemOption{
		hardware.WithSignalReport(true),
		hardware.WithLogger(discardLogger()),
	}

	m := hardware.NewKissModem(tr, append(base, opts...)...)

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := m.Connect(ctx); err != nil {
		t.Fatalf("Connect: %v", err)
	}

	// Connect configures signal reporting, so the modem answers with 0x9A and
	// the enabled flag. Checking it here means every later test starts aligned.
	f := tr.expectFrame(t)
	if len(f.Data) != 2 || f.Data[0] != hardware.HwResp(hardware.HW_CMD_SET_SIGNAL_REPORT) {
		t.Fatalf("signal report reply = % x, want 9A 01", f.Data)
	}

	if f.Data[1] != 1 {
		t.Fatalf("signal report enabled = %d, want 1", f.Data[1])
	}

	return m
}

// TestConnectAndConfigure is the contract that matters most: the sequence
// meshcore-go performs on startup must be understood by the firmware, and the
// firmware's acknowledgements must satisfy the client.
func TestConnectAndConfigure(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))
	modem := newModem(t, tr)

	cfg := &hardware.RadioConfig{
		FreqHz: 869618000,
		BwHz:   62500,
		SF:     8,
		CR:     8,
	}

	if err := modem.SetRadio(cfg); err != nil {
		t.Fatalf("SetRadio: %v", err)
	}

	f := tr.expectFrame(t)
	if f.Command != hardware.KISS_CMD_SETHARDWARE || len(f.Data) == 0 ||
		f.Data[0] != hardware.HW_RESP_OK {
		t.Fatalf("SetRadio reply = % x, want F0", f.Data)
	}

	if err := modem.SetTxPower(17); err != nil {
		t.Fatalf("SetTxPower: %v", err)
	}

	f = tr.expectFrame(t)
	if f.Command != hardware.KISS_CMD_SETHARDWARE || len(f.Data) == 0 ||
		f.Data[0] != hardware.HW_RESP_OK {
		t.Fatalf("SetTxPower reply = % x, want F0", f.Data)
	}

	// Read the radio back, so the firmware's own encoding of the configuration
	// is decoded by meshcore-go and must round trip exactly.
	frame := hardware.EncodeHardwareFrame(0, hardware.HW_CMD_GET_RADIO, nil)
	if err := tr.Send(frame); err != nil {
		t.Fatalf("send GetRadio: %v", err)
	}

	f = tr.expectFrame(t)
	if len(f.Data) == 0 || f.Data[0] != hardware.HwResp(hardware.HW_CMD_GET_RADIO) {
		t.Fatalf("GetRadio reply = % x, want sub-command 0x%02X",
			f.Data, hardware.HwResp(hardware.HW_CMD_GET_RADIO))
	}

	got, err := hardware.RadioConfigFromBytes(f.Data[1:])
	if err != nil {
		t.Fatalf("RadioConfigFromBytes: %v", err)
	}

	if *got != *cfg {
		t.Fatalf("radio config round trip: got %+v want %+v", *got, *cfg)
	}
}

// TestGetDeviceName covers a variable-length reply rather than a fixed one.
func TestGetDeviceName(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))
	newModem(t, tr)

	frame := hardware.EncodeHardwareFrame(0, hardware.HW_CMD_GET_DEVICE_NAME, nil)
	if err := tr.Send(frame); err != nil {
		t.Fatalf("send: %v", err)
	}

	f := tr.expectFrame(t)
	if len(f.Data) == 0 ||
		f.Data[0] != hardware.HwResp(hardware.HW_CMD_GET_DEVICE_NAME) {
		t.Fatalf("device name reply = % x", f.Data)
	}

	name := string(f.Data[1:])
	if name == "" {
		t.Fatal("device name is empty")
	}

	t.Logf("modem reports itself as %q", name)
}

// TestDataFrameAndSignalReport checks the pairing rule and the quarter-dB SNR
// encoding, by having the fake radio receive a packet.
func TestDataFrameAndSignalReport(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))
	modem := newModem(t, tr)

	type rx struct {
		data  []byte
		snr   float32
		rssi  int8
		known bool
	}

	got := make(chan rx, 4)

	modem.SetDataHandler(func(data []byte, snr float32, rssi int8, ok bool) {
		got <- rx{append([]byte(nil), data...), snr, rssi, ok}
	})

	// Includes both delimiters, so the round trip proves the modem escapes and
	// the client unescapes correctly.
	payload := []byte{0x01, 0x02, 0xC0, 0xDB, 0xFF}

	// 40 in quarter-dB steps is 10 dB, and -84 dBm.
	tr.control(t, "rx:0102C0DBFF@40,-84")

	select {
	case r := <-got:
		if !r.known {
			t.Fatal("signal info not populated; data and RxMeta were not paired")
		}

		if !bytes.Equal(r.data, payload) {
			t.Fatalf("payload = % x, want % x", r.data, payload)
		}

		if r.snr != 10 {
			t.Fatalf("snr = %v dB, want 10", r.snr)
		}

		if r.rssi != -84 {
			t.Fatalf("rssi = %d dBm, want -84", r.rssi)
		}
	case <-time.After(frameTimeout):
		t.Fatal("no data frame delivered")
	}
}

// TestDataFrameWithoutSignalReport checks the modem honours a client that turns
// signal reporting off, and still delivers the packet.
func TestDataFrameWithoutSignalReport(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))

	// Built without WithSignalReport, so Connect asks the modem to disable it.
	modem := hardware.NewKissModem(tr, hardware.WithLogger(discardLogger()))

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := modem.Connect(ctx); err != nil {
		t.Fatalf("Connect: %v", err)
	}

	f := tr.expectFrame(t)
	if len(f.Data) != 2 || f.Data[1] != 0 {
		t.Fatalf("signal report reply = % x, want 99 00", f.Data)
	}

	type rx struct {
		data  []byte
		known bool
	}

	got := make(chan rx, 4)

	modem.SetDataHandler(func(data []byte, snr float32, rssi int8, ok bool) {
		got <- rx{append([]byte(nil), data...), ok}
	})

	tr.control(t, "rx:AABBCC")

	select {
	case r := <-got:
		if !bytes.Equal(r.data, []byte{0xAA, 0xBB, 0xCC}) {
			t.Fatalf("payload = % x, want aabbcc", r.data)
		}

		if r.known {
			t.Fatal("signal info present although the client disabled signal reporting")
		}
	case <-time.After(frameTimeout):
		t.Fatal("no data frame delivered")
	}
}

// TestSendDataCompletesOnTxDone proves the firmware's TxDone satisfies the
// client's flow control, so a transmission does not stall.
func TestSendDataCompletesOnTxDone(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))

	modem := newModem(t, tr,
		hardware.WithTxFlowControl(5*time.Second))

	tr.control(t, "airtime:180")

	done := make(chan error, 1)
	go func() { done <- modem.SendData([]byte{0x01, 0x02, 0x03}) }()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("SendData: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("SendData did not complete after TxDone")
	}
}

// TestSendDataReportsFailure checks that a transmission the modem refused
// surfaces as an error rather than a silent no-op.
//
// Each case needs its own client: meshcore-go holds the TxDone slot after a
// failed send and only releases it on reconnect, so the next SendData on the
// same client returns ErrTxPending by design.
func TestSendDataReportsFailure(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))

	modem := newModem(t, tr,
		hardware.WithTxFlowControl(2*time.Second))

	// A zero time on air is how the firmware reports CSMA giving up.
	tr.control(t, "failtx")

	done := make(chan error, 1)
	go func() { done <- modem.SendData([]byte{0x01, 0x02, 0x03}) }()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("SendData returned nil, want an error when the modem reports failure")
		}

		t.Logf("failure surfaced as: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("SendData neither returned nor timed out after a reported failure")
	}
}

// TestFailedTxReleasesSlot pins how the client behaves after the modem reports
// a failed transmission: completeTx clears the pending slot, so a retry is
// allowed. ErrTxPending is reserved for a send whose TxDone never arrived, which
// is what the busy case below produces.
func TestFailedTxReleasesSlot(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))

	modem := newModem(t, tr,
		hardware.WithTxFlowControl(2*time.Second))

	tr.control(t, "failtx")

	if err := modem.SendData([]byte{0x01}); err == nil {
		t.Fatal("first SendData returned nil, want an error")
	}

	// The slot must be free again, so this must not be ErrTxPending.
	tr.control(t, "airtime:150")

	done := make(chan error, 1)
	go func() { done <- modem.SendData([]byte{0x02}) }()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("retry after a reported failure = %v, want success", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("retry after a reported failure did not complete")
	}
}

// TestSendDataWhileBusyIsRefused checks the modem rejects a transmission while
// the radio is busy, with an explicit error rather than by staying silent.
func TestSendDataWhileBusyIsRefused(t *testing.T) {
	tr := newPipeTransport(t, serverPath(t))

	modem := newModem(t, tr,
		hardware.WithTxFlowControl(2*time.Second))

	tr.control(t, "busy:1")

	done := make(chan error, 1)
	go func() { done <- modem.SendData([]byte{0xAA}) }()

	// The refusal is an ERROR frame carrying TX_BUSY.
	f := tr.expectFrame(t)
	if len(f.Data) != 2 || f.Data[0] != hardware.HW_RESP_ERROR {
		t.Fatalf("busy refusal = % x, want F1 07", f.Data)
	}

	if f.Data[1] != hardware.HW_ERR_TX_BUSY {
		t.Fatalf("busy error code = 0x%02X, want 0x%02X", f.Data[1],
			hardware.HW_ERR_TX_BUSY)
	}

	// No TxDone will arrive, so the client is entitled to give up.
	select {
	case err := <-done:
		t.Logf("client gave up with: %v", err)
	case <-time.After(5 * time.Second):
		t.Fatal("client never finished waiting for a TxDone that was refused")
	}

	// Once the radio is idle again, sending works.
	tr.control(t, "busy:0")

	done2 := make(chan error, 1)
	go func() { done2 <- modem.SendData([]byte{0xBB}) }()

	select {
	case err := <-done2:
		if err != nil && !errors.Is(err, hardware.ErrTxPending) {
			t.Fatalf("SendData after going idle = %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("SendData after going idle did not complete")
	}
}
