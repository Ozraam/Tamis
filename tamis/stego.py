"""A hidden mark: a short text written invisibly into an export's pixels, and read back.

Each 8×8 block of the picture's luminance carries one bit, in two low-to-mid
frequency DCT coefficients (quantisation index modulation): a change of at most
a few levels per pixel, and a step large enough for the bits to survive saving
as JPEG down to a quality of about 60. The payload (length, up to MAX_BYTES of
UTF-8 and a CRC-16) is repeated over every block of the picture and read back by
majority vote, so clipped highlights or a few damaged blocks don't matter.

It is a mark, not a protection: resizing, cropping, rotating or heavy
filtering lose it, since the blocks no longer line up. Works on h×w×3 float
arrays in the 0–255 range, whatever the bit depth they will be written at.
"""
import numpy as np

MAX_BYTES = 32
# Quantisation step of the carrying coefficients, in 8-bit levels. 16 changes pixels by 0.7 level on
# average (3.6 at most), and survives JPEG down to quality 60; 12 failed at 60, 20 is no more robust.
STEP = 16.0
_COEFFICIENTS = ((1, 2), (2, 1))  # (row, column) frequencies of the 8×8 DCT
_BITS = 8 + MAX_BYTES * 8 + 16
_LUMA = np.array([0.299, 0.587, 0.114])


def _dct_matrix():
    n = np.arange(8)
    matrix = np.sqrt(2 / 8) * np.cos(np.pi * (2 * n[None, :] + 1) * n[:, None] / 16)
    matrix[0] /= np.sqrt(2)
    return matrix


_DCT = _dct_matrix()
_BASES = [np.outer(_DCT[u], _DCT[v]) for u, v in _COEFFICIENTS]  # orthonormal: coefficient = sum(block * basis)


def _crc16(data):
    """CRC-16/CCITT-FALSE."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def problem(text):
    """Why `text` can't be hidden, None if it can."""
    if len(text.encode("utf-8")) > MAX_BYTES:
        return f"The hidden mark is too long: {MAX_BYTES} bytes at most (accented letters take two)."
    return None


def _payload_bits(text):
    data = text.encode("utf-8")[:MAX_BYTES]
    padded = data + bytes(MAX_BYTES - len(data))
    message = bytes([len(data)]) + padded + _crc16(data).to_bytes(2, "big")
    return np.unpackbits(np.frombuffer(message, dtype=np.uint8))


def _blocks(height, width):
    return height // 8, width // 8


def capacity(height, width):
    """How many times the payload fits in a picture of that size (it needs at least once)."""
    rows, columns = _blocks(height, width)
    return rows * columns // _BITS


def _luma_blocks(pixels, rows, columns):
    luma = pixels[:rows * 8, :columns * 8] @ _LUMA
    return luma.reshape(rows, 8, columns, 8).transpose(0, 2, 1, 3)  # rows × columns × 8 × 8


def embed(pixels, text):
    """Hide `text` in `pixels` (float h×w×3, 0–255, changed in place); returns whether it fits.

    The luminance change is added to all three channels alike, so colours stay as they were.
    """
    height, width = pixels.shape[:2]
    if not text or capacity(height, width) < 1:
        return False
    rows, columns = _blocks(height, width)
    bits = _payload_bits(text)
    wanted = np.resize(bits, rows * columns).reshape(rows, columns).astype(np.float64)
    blocks = _luma_blocks(pixels, rows, columns)
    change = np.zeros((rows, columns, 8, 8))
    for basis in _BASES:
        coefficient = np.einsum("rcij,ij->rc", blocks, basis)
        offset = wanted * STEP / 2  # bit 0 on multiples of STEP, bit 1 halfway between
        target = np.round((coefficient - offset) / STEP) * STEP + offset
        change += (target - coefficient)[..., None, None] * basis
    pixels[:rows * 8, :columns * 8] += change.transpose(0, 2, 1, 3).reshape(rows * 8, columns * 8)[..., None]
    np.clip(pixels, 0, 255, out=pixels)
    return True


def read(pixels):
    """The text hidden in `pixels` (float or integer h×w×3 in 0–255), None if there is none to be found."""
    height, width = pixels.shape[:2]
    if capacity(height, width) < 1:
        return None
    rows, columns = _blocks(height, width)
    blocks = _luma_blocks(pixels.astype(np.float64), rows, columns)
    votes = np.zeros((rows, columns))
    for basis in _BASES:
        coefficient = np.einsum("rcij,ij->rc", blocks, basis)
        phase = np.mod(coefficient, STEP) / STEP  # 0 (bit 0) … 0.5 (bit 1) … 1 (bit 0)
        votes += np.where(np.abs(phase - 0.5) < 0.25, 1.0, -1.0)
    flat = votes.ravel()
    count = len(flat) // _BITS * _BITS
    totals = flat[:count].reshape(-1, _BITS).sum(axis=0)
    message = np.packbits((totals > 0).astype(np.uint8)).tobytes()
    length, data, crc = message[0], message[1:1 + MAX_BYTES], int.from_bytes(message[-2:], "big")
    if length == 0 or length > MAX_BYTES or _crc16(data[:length]) != crc:
        return None
    try:
        return data[:length].decode("utf-8")
    except UnicodeDecodeError:
        return None
