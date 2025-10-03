class Compressor:
    index_table = [
        -1, -1, -1, -1, 2, 4, 6, 8,
        -1, -1, -1, -1, 2, 4, 6, 8
    ]

    step_size_table = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
        19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
        50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
        130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
        337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
        876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
        2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
        5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
        15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
    ]

    def __init__(self):
        self.predicted = 0
        self.index = 0

    def encode_sample(self, sample):
        delta = sample - self.predicted
        sign = 0
        step = self.step_size_table[self.index]

        if delta < 0:
            sign = 8
            delta = -delta

        diff = step >> 3
        code = 0
        if delta >= step:
            code = 4
            delta -= step
            diff += step
        step >>= 1
        if delta >= step:
            code |= 2
            delta -= step
            diff += step
        step >>= 1
        if delta >= step:
            code |= 1
            diff += step

        code |= sign
        new_predicted = self.predicted + (-diff if sign != 0 else diff)
        self.predicted = max(-32768, min(32767, new_predicted))

        new_index = self.index + self.index_table[code]
        self.index = max(0, min(88, new_index))

        return code

    def decode_nibble(self, code):
        step = self.step_size_table[self.index]
        diff = step >> 3
        if code & 4:
            diff += step
        if code & 2:
            diff += step >> 1
        if code & 1:
            diff += step >> 2

        new_predicted = self.predicted + (-diff if (code & 8) else diff)
        self.predicted = max(-32768, min(32767, new_predicted))

        new_index = self.index + self.index_table[code]
        self.index = max(0, min(88, new_index))

        return self.predicted

    def encode(self, pcm_bytes):
        """
        pcm_bytes: bytes or bytearray of 16-bit PCM samples (little endian).
        Returns bytearray ADPCM compressed data.
        """
        adpcm = bytearray(len(pcm_bytes) // 4)  # 2 samples (4 bytes) => 1 byte ADPCM
        out_index = 0
        i = 0
        self.predicted = 0
        self.index = 0

        while i + 3 < len(pcm_bytes):
            sample1 = pcm_bytes[i] | (pcm_bytes[i+1] << 8)
            if sample1 & 0x8000:
                sample1 -= 0x10000

            sample2 = pcm_bytes[i+2] | (pcm_bytes[i+3] << 8)
            if sample2 & 0x8000:
                sample2 -= 0x10000

            n1 = self.encode_sample(sample1)
            n2 = self.encode_sample(sample2)

            adpcm[out_index] = (n2 << 4) | n1
            out_index += 1
            i += 4

        return adpcm

    def decode(self, adpcm_bytes):
        """
        adpcm_bytes: bytes or bytearray of ADPCM compressed data.
        Returns bytearray PCM 16-bit samples (little endian).
        """
        pcm_bytes = bytearray(len(adpcm_bytes) * 4)
        out_index = 0
        self.predicted = 0
        self.index = 0

        for b in adpcm_bytes:
            nibble1 = b & 0x0F
            nibble2 = (b >> 4) & 0x0F

            s1 = self.decode_nibble(nibble1)
            pcm_bytes[out_index] = s1 & 0xFF
            pcm_bytes[out_index+1] = (s1 >> 8) & 0xFF
            out_index += 2

            s2 = self.decode_nibble(nibble2)
            pcm_bytes[out_index] = s2 & 0xFF
            pcm_bytes[out_index+1] = (s2 >> 8) & 0xFF
            out_index += 2

        return pcm_bytes
