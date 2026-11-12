///|
/// SHA-256（FIPS 180-4）纯 MoonBit 实现。
///
/// 事务 intent 需要记录数据文件与正式 JSON commit 的摘要，用于恢复时判定
/// “目标版本上的 commit 是否就是本事务写出的那个”。实现放在 MoonBit 里，
/// JS/Wasm 目标也能编译（虽然本版本的事务写入只在 Native 提供）。
/// 不用哈希代替锁或唯一性：txn ID 仍来自操作系统随机源。

///|
fn sha256_rotr(x : UInt, n : Int) -> UInt {
  (x >> n) | (x << (32 - n))
}

///|
fn sha256_constants() -> Array[UInt] {
  [
    0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U,
    0x3956C25BU, 0x59F111F1U, 0x923F82A4U, 0xAB1C5ED5U,
    0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U,
    0x72BE5D74U, 0x80DEB1FEU, 0x9BDC06A7U, 0xC19BF174U,
    0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU,
    0x2DE92C6FU, 0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU,
    0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
    0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U,
    0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU, 0x53380D13U,
    0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U,
    0xA2BFE8A1U, 0xA81A664BU, 0xC24B8B70U, 0xC76C51A3U,
    0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U,
    0x19A4C116U, 0x1E376C08U, 0x2748774CU, 0x34B0BCB5U,
    0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
    0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U,
    0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U, 0xC67178F2U,
  ]
}

///|
fn sha256_init_state() -> Array[UInt] {
  [
    0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
    0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U,
  ]
}

///|
/// 完整消息摘要。返回 32 字节。
fn sha256_bytes(data : Bytes) -> Bytes {
  let padded_len = ((data.length() + 9 + 63) / 64) * 64
  let padded : Array[Byte] = Array(capacity=padded_len)
  for i in 0..<data.length() {
    padded.push(data[i])
  }
  padded.push((128).to_byte())
  while padded.length() % 64 != 56 {
    padded.push((0).to_byte())
  }
  let bits = data.length().to_uint64() * 8UL
  for byte in bits.to_be_bytes() {
    padded.push(byte)
  }
  let constants = sha256_constants()
  let state = sha256_init_state()
  let mut block = 0
  while block * 64 < padded.length() {
    let words : Array[UInt] = Array(capacity=64)
    let base = block * 64
    for i in 0..<16 {
      let at = base + i * 4
      words.push(
        (padded[at].to_uint() << 24) |
        (padded[at + 1].to_uint() << 16) |
        (padded[at + 2].to_uint() << 8) |
        padded[at + 3].to_uint(),
      )
    }
    for i in 16..<64 {
      let w15 = words[i - 15]
      let w2 = words[i - 2]
      let s0 = sha256_rotr(w15, 7) ^ sha256_rotr(w15, 18) ^ (w15 >> 3)
      let s1 = sha256_rotr(w2, 17) ^ sha256_rotr(w2, 19) ^ (w2 >> 10)
      words.push(words[i - 16] + s0 + words[i - 7] + s1)
    }
    let mut a = state[0]
    let mut b = state[1]
    let mut c = state[2]
    let mut d = state[3]
    let mut e = state[4]
    let mut f = state[5]
    let mut g = state[6]
    let mut h = state[7]
    for i in 0..<64 {
      let s1 = sha256_rotr(e, 6) ^ sha256_rotr(e, 11) ^ sha256_rotr(e, 25)
      let ch = (e & f) ^ (e.lnot() & g)
      let temp1 = h + s1 + ch + constants[i] + words[i]
      let s0 = sha256_rotr(a, 2) ^ sha256_rotr(a, 13) ^ sha256_rotr(a, 22)
      let maj = (a & b) ^ (a & c) ^ (b & c)
      let temp2 = s0 + maj
      h = g
      g = f
      f = e
      e = d + temp1
      d = c
      c = b
      b = a
      a = temp1 + temp2
    }
    state[0] = state[0] + a
    state[1] = state[1] + b
    state[2] = state[2] + c
    state[3] = state[3] + d
    state[4] = state[4] + e
    state[5] = state[5] + f
    state[6] = state[6] + g
    state[7] = state[7] + h
    block = block + 1
  }
  let out : Array[Byte] = Array(capacity=32)
  for word in state {
    let bytes = word.to_uint64().to_be_bytes()
    for i in 4..<8 {
      out.push(bytes[i])
    }
  }
  Bytes::from_array(out)
}

///|
/// 小写十六进制编码。
fn hex_of_bytes(data : Bytes) -> String {
  let digits : Array[Char] = [
    '0', '1', '2', '3', '4', '5', '6', '7',
    '8', '9', 'a', 'b', 'c', 'd', 'e', 'f',
  ]
  let sb = StringBuilder()
  for byte in data {
    let value = byte.to_int()
    sb.write_char(digits[value / 16])
    sb.write_char(digits[value % 16])
  }
  sb.to_string()
}

///|
/// 小写十六进制 SHA-256 摘要（64 个字符）。
fn sha256_hex(data : Bytes) -> String {
  hex_of_bytes(sha256_bytes(data))
}
