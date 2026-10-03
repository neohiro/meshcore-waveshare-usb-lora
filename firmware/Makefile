PREFIX		?= arm-none-eabi
SIZE		:= $(PREFIX)-size
CXX			:= $(PREFIX)-g++
CC			:= $(PREFIX)-gcc
LD			:= $(PREFIX)-ld
OBJCOPY		:= $(PREFIX)-objcopy
OBJDUMP		:= $(PREFIX)-objdump
MKDIR_P		:= mkdir -p

ARCH_FLAGS = -mthumb -mcpu=cortex-m3 -msoft-float -mfix-cortex-m3-ldrd
CFLAGS = -Wall -Wextra -Wno-unused-parameter -I. -Isrc -Isx126x -Irtos -Ilibopencm3/include -nostdlib $(ARCH_FLAGS) -Os -DSTM32F1
CFLAGS += -fno-common -ffunction-sections -fdata-sections

# Emit a .su file per function with its stack frame size. Costs nothing at run
# time and is what tests/test_stack_usage.py reads, so a large local buffer can
# be caught before it silently overruns a task stack on the hardware.
CFLAGS += -fstack-usage

# TCXO variant of the board (SKU USB-TO-LoRa-xF). The XTAL variant
# (SKU USB-TO-LoRa-xF-B) needs WITH_TCXO=0.
WITH_TCXO	?= 1
CFLAGS += -DWITH_TCXO=$(WITH_TCXO)

# Header dependency tracking.
#
# The pattern rule below can only see the .c file and this Makefile, so without
# these flags an edit to a header rebuilds nothing. An incremental build then
# links stale objects against new ones and produces a binary that matches no
# source state -- the worst kind of build bug, because it looks fine.
CFLAGS += -MMD -MP

LDSCRIPT = gd32f103.ld


OUT_DIR = obj

ifneq ($(V), 1)
	Q := @
endif

LIBFREERTOS = libfreertos.a

RTOS_OBJS = heap_4.o list.o port.o tasks.o queue.o opencm3.o
RTOS_OBJS := $(patsubst %.o, obj/%.o, $(RTOS_OBJS))

LIBSX126X = libsx126x.a

SX126X_OBJS = sx126x.o sx126x_driver_version.o lr_fhss_mac.o sx126x_lr_fhss.o sx126x_hal.o
SX126X_OBJS := $(patsubst %.o, obj/%.o, $(SX126X_OBJS))

FIRMWARE = firmware.elf
BINARY = firmware.bin
MEMORYMAP = firmware.map

OBJS = main.o init.o radio.o serial.o kiss.o kiss_frame.o device_id.o lora_params.o pa_config.o mem.o
OBJS := $(patsubst %.o, obj/%.o, $(OBJS))

vpath %.c src/ rtos/ sx126x/

# Side-effect files written by -MMD, one per object.
#
# OBJS and friends already carry the obj/ prefix, so this only swaps the
# extension. Wrapping them in another $(patsubst %.o, $(OUT_DIR)/%.d, ...) would
# yield obj/obj/*.d, and -include ignores files it cannot find -- which fails
# silently, with the build quietly reverting to having no dependencies at all.
DEPS = $(OBJS:.o=.d) $(RTOS_OBJS:.o=.d) $(SX126X_OBJS:.o=.d)

all: $(BINARY)

$(BINARY): $(FIRMWARE)
	@echo [BN]	$@
	$(Q)$(OBJCOPY) -O binary $< $@

$(FIRMWARE): $(OUT_DIR) libopencm3 $(LIBFREERTOS) $(LIBSX126X) $(OBJS)
	@echo [CC]	$@
	$(Q)$(CC) -o $@ $(OBJS) -T gd32f103.ld -L. -Llibopencm3/lib --specs=nosys.specs -nostartfiles -Xlinker --gc-sections -Xlinker --print-memory-usage -Wl,-Map,"$(MEMORYMAP)" -lnosys -lsx126x -lfreertos -lopencm3_stm32f1

libopencm3: libopencm3/lib/libopencm3_stm32f1.a

libopencm3/lib/libopencm3_stm32f1.a:
	@echo [MK] $@
	$(Q)$(MAKE) -C libopencm3 TARGETS=stm32/f1

$(LIBFREERTOS): $(RTOS_OBJS)
	@echo [LD] 	$@
	$(Q)$(LD) -r -o $@ $^

$(LIBSX126X): $(SX126X_OBJS)
	@echo [LD]	$@
	$(Q)$(LD) -r -o $@ $^

$(OUT_DIR):
	$(Q)$(MKDIR_P) $(OUT_DIR)

$(OUT_DIR)/%.o: %.c Makefile
	@echo [CC]	$<
	$(Q)$(CC) $(CFLAGS) -c -o $@ $<

# Missing .d files are expected on a first build, so this must not fail.
-include $(DEPS)

clean:
	@echo [RM] $(BINARY)
	$(Q)rm -f $(BINARY)
	@echo [RM] $(FIRMWARE)
	$(Q)rm -f $(FIRMWARE)
	@echo [RM] $(MEMORYMAP)
	$(Q)rm -f $(MEMORYMAP)
	@echo [RM] $(LIBSX126X)
	$(Q)rm -f $(LIBSX126X)
	@echo [RM] $(LIBFREERTOS)
	$(Q)rm -f $(LIBFREERTOS)
	@echo [RM] $(OUT_DIR)
	$(Q)rm -rf $(OUT_DIR)