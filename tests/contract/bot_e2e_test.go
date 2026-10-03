// End-to-end check of the whole product: the real meshcore-bot, reading the real
// bot/config.toml, connected to the real firmware's protocol code over TCP.
//
// Everything below the config file is exercised end to end: the bot's TOML
// parser, its transport, its KISS framing, this firmware's parser, its command
// handling and the replies it writes back. Only the radio and the physical USB
// serial port are absent.
//
// The firmware has to be the TCP *server* because a MeshCore host dials a TCP
// modem rather than listening, which is also why the README's tcp:// scheme
// works and why it needs something on the far end.
package contract

import (
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/meshcore-go/meshcore-go/hardware"
)

// botPath is supplied by tools/ci.ps1, which installs the published bot.
func botPath(t *testing.T) string {
	t.Helper()

	p := os.Getenv("MESHCORE_BOT_BIN")
	if p == "" {
		t.Skip("MESHCORE_BOT_BIN not set; run through tools/ci.ps1")
	}

	if _, err := os.Stat(p); err != nil {
		t.Fatalf("meshcore-bot not found at %s: %v", p, err)
	}

	return p
}

var listenRe = regexp.MustCompile(`listening on 127\.0\.0\.1:(\d+)`)

// parsePort extracts the port the modem actually bound. The server is asked for
// port 0 so the OS picks a free one: reserving a port here, closing it, and
// handing the number to another process is a race, and under load the port can
// be gone before that process binds.
//
// The modem therefore reports the port it bound, not the one it was asked for.
// Port 0 is a real request rather than "unset", so the option parsing has to
// keep those two cases apart.
func parsePort(log string) int {
	m := listenRe.FindStringSubmatch(log)

	if m == nil {
		return 0
	}

	port, err := strconv.Atoi(m[1])
	if err != nil || port <= 0 {
		return 0
	}

	return port
}

func waitForPort(t *testing.T, log *syncBuffer) int {
	t.Helper()

	waitFor(t, "the modem to report a bound port", 10*time.Second, func() bool {
		return parsePort(log.String()) != 0
	})

	return parsePort(log.String())
}

// syncBuffer collects a process' output from a goroutine so the test can watch
// for a pattern while the process runs.
type syncBuffer struct {
	mu  sync.Mutex
	buf strings.Builder
}

func (b *syncBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *syncBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func waitFor(t *testing.T, what string, within time.Duration, has func() bool) {
	t.Helper()

	deadline := time.Now().Add(within)

	for time.Now().Before(deadline) {
		if has() {
			return
		}
		time.Sleep(25 * time.Millisecond)
	}

	t.Fatalf("timed out after %s waiting for %s", within, what)
}

// TestServerReportsItsBoundPort guards the mechanism the acceptance test relies
// on. If the modem reported the port it was asked for rather than the one it
// bound, every test would have to guess a port and reserve it, which is a race.
//
// This needs no bot, so it stays fast and always runs.
func TestServerReportsItsBoundPort(t *testing.T) {
	log := &syncBuffer{}

	cmd := exec.Command(serverPath(t), "--tcp", "0")
	cmd.Stderr = log
	cmd.Stdout = log

	if err := cmd.Start(); err != nil {
		t.Fatalf("start kiss server: %v", err)
	}

	t.Cleanup(func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	})

	port := waitForPort(t, log)

	// The reported port must be the real one.
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 5*time.Second)
	if err != nil {
		t.Fatalf("modem reported port %d but it is not connectable: %v", port, err)
	}

	// And the modem must answer a real KISS request over it, so this exercises
	// the whole path rather than just the socket.
	frame := hardware.EncodeHardwareFrame(0, hardware.HW_CMD_PING, nil)

	if _, err := conn.Write(frame); err != nil {
		t.Fatalf("write ping: %v", err)
	}

	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))

	buf := make([]byte, 256)
	n, err := conn.Read(buf)
	if err != nil {
		t.Fatalf("read reply: %v", err)
	}

	f, err := hardware.DecodeFrame(buf[:n])
	if err != nil {
		t.Fatalf("DecodeFrame: %v", err)
	}

	if len(f.Data) == 0 || f.Data[0] != hardware.HwResp(hardware.HW_CMD_PING) {
		t.Fatalf("ping reply = % x, want a pong", f.Data)
	}

	_ = conn.Close()

	t.Logf("modem bound and answered on 127.0.0.1:%d", port)
}

// TestRealBotAgainstRealFirmware is the acceptance test for the deliverable.
func TestRealBotAgainstRealFirmware(t *testing.T) {
	bot := botPath(t)

	serverLog := &syncBuffer{}

	// Port 0 asks the OS for any free port; the server reports which one it
	// actually got, which removes the reserve-then-rebind race.
	server := exec.Command(serverPath(t), "--tcp", "0", "--trace")
	server.Stderr = serverLog
	server.Stdout = serverLog

	if err := server.Start(); err != nil {
		t.Fatalf("start kiss server: %v", err)
	}

	t.Cleanup(func() {
		_ = server.Process.Kill()
		_ = server.Wait()
	})

	port := waitForPort(t, serverLog)

	// A copy of the shipped config with only the transport changed, so this
	// exercises the real values a user would run rather than a fixture.
	// Go runs tests with the package directory as the working directory, so the
	// project root is two levels up.
	shipped, err := os.ReadFile(filepath.Join("..", "..", "bot", "config.toml"))
	if err != nil {
		t.Fatalf("read bot/config.toml: %v", err)
	}

	cfg := regexp.MustCompile(`(?m)^connection\s*=\s*".*"\s*$`).
		ReplaceAllString(string(shipped),
			fmt.Sprintf("connection = \"tcp://127.0.0.1:%d\"", port))

	if cfg == string(shipped) {
		t.Fatal("could not rewrite the connection line in bot/config.toml")
	}

	dir := t.TempDir()
	cfgPath := filepath.Join(dir, "config.toml")

	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatalf("write config: %v", err)
	}

	botLog := &syncBuffer{}

	proc := exec.Command(bot, "--config", cfgPath, "-v")
	proc.Stdout = botLog
	proc.Stderr = botLog

	if err := proc.Start(); err != nil {
		t.Fatalf("start meshcore-bot: %v", err)
	}

	t.Cleanup(func() {
		_ = proc.Process.Kill()
		_ = proc.Wait()
	})

	// The modem must see the bot's own setup sequence.
	waitFor(t, "SET_RADIO to reach the firmware", 20*time.Second, func() bool {
		return strings.Contains(serverLog.String(), "sub=09")
	})

	waitFor(t, "SET_TX_POWER to reach the firmware", 10*time.Second, func() bool {
		return strings.Contains(serverLog.String(), "sub=0a")
	})

	// And the firmware must have acknowledged, which is what proves the round
	// trip rather than one direction. An OK is FEND, type 0x06, the 0xF0
	// response byte, FEND.
	waitFor(t, "the modem to acknowledge", 10*time.Second, func() bool {
		return strings.Contains(serverLog.String(), "tx 4 bytes: c0 06 f0 c0")
	})

	waitFor(t, "the bot to report it started", 20*time.Second, func() bool {
		return strings.Contains(botLog.String(), "started bot")
	})

	// A config the bot cannot use must not be silently tolerated. This one is
	// byte-for-byte the shipped config, so a mismatch here is a real defect.
	for _, want := range []string{
		"SET_RADIO freq=869.618 bw=62.5 sf=8 cr=8",
		"SET_TX_POWER tx=17",
	} {
		if !strings.Contains(botLog.String(), want) {
			t.Errorf("bot log does not contain %q\n--- bot log ---\n%s",
				want, botLog.String())
		}
	}

	// The duty cycle has to be honoured, or an EU868 node transmits without
	// limit.
	if !strings.Contains(botLog.String(), "duty_cycle_pct=1") {
		t.Errorf("bot did not apply dutyCycle from the config\n--- bot log ---\n%s",
			botLog.String())
	}

	t.Logf("firmware saw:\n%s", serverLog.String())
	t.Logf("bot saw:\n%s", botLog.String())
}
