MEMORY
{
	/*
	 * Upstream places the application at 0x08004000 because its custom
	 * bootloader lives at 0x08000000. This build is flashed over the
	 * GD32F103 USART ISP bootloader with no intermediate bootloader, so the
	 * image belongs at the reset vector address.
	 *
	 * LENGTH is 64K, not upstream's 112K: this target is a GD32F103C8T6,
	 * which has 64 KiB of flash and 20 KiB of SRAM. Leaving the larger figure
	 * here would let the linker accept an image the chip cannot hold, turning
	 * a build-time mistake into an obscure failure at flash time. The size is
	 * tied to the pyOCD target by tests/test_linker_geometry.py.
	 */
	rom (rx) : ORIGIN = 0x08000000, LENGTH = 64K
	ram (rwx) : ORIGIN = 0x20000000, LENGTH = 20K
}

INCLUDE cortex-m-generic.ld
