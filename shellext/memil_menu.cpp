// 스토어판(MSIX)의 탐색기 우클릭 메뉴.
//
// 패키지 앱이 레지스트리에 적는 메뉴 항목은 탐색기에 보이지 않는다. 대신 패키지
// 매니페스트에 이 DLL 의 COM 클래스를 등록하면 탐색기가 메뉴를 만들 때 불러 쓴다.
//
//   * NewNote   : 바탕 화면 빈 곳의 '새 메모 추가'     → SyncNotes.exe --new-note --at-cursor
//   * NewFolder : 바탕 화면 빈 곳의 '새 메모 폴더 추가' → SyncNotes.exe --new-folder --at-cursor
//   * LinkNote  : 파일과 폴더의 '새 메모에 링크로 넣기' → SyncNotes.exe --link-note "경로" ...
//   * ClipNote  : 바탕 화면과 파일의 '클립보드로 새 메모' → SyncNotes.exe --clip-note --at-cursor
//
// 메뉴를 보일지와 메뉴의 글자는 앱이 적어 두는 파일 하나로 정한다
// (사용자 폴더\MemilMemo\shell-menu.txt, UTF-8, 첫 줄이 '새 메모 추가', 둘째 줄이
// '새 메모에 링크로 넣기', 셋째 줄이 '새 메모 폴더 추가', 넷째 줄이 '클립보드로 새 메모').
// 파일이 없으면 메뉴를
// 감춘다. 앱의 설정에서 켜고 끈다.
//
// 메뉴를 그리는 동안 불리므로 빠르게 끝나야 한다. 작은 파일 하나만 읽는다.

#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <shobjidl_core.h>

#include <new>
#include <string>
#include <vector>

namespace {

HMODULE g_module = nullptr;
long g_objects = 0;

// tools/make_msix.py 의 매니페스트와 같아야 한다.
const CLSID CLSID_NewNote = {0x75353010, 0x835E, 0x4152,
                             {0x9B, 0x9C, 0x49, 0x85, 0xF6, 0xDD, 0x6C, 0x77}};
const CLSID CLSID_LinkNote = {0x82DA3587, 0xDE72, 0x4930,
                              {0x89, 0x43, 0x9A, 0xD6, 0xAA, 0xDB, 0x32, 0xC3}};
const CLSID CLSID_NewFolder = {0xC3A1F6B2, 0x5D0E, 0x4B7A,
                               {0x9E, 0x64, 0x2F, 0x81, 0xD7, 0xC0, 0x5A, 0x39}};
const CLSID CLSID_ClipNote = {0x5B8E2C47, 0x1D3A, 0x4F60,
                              {0xA2, 0x9B, 0x6E, 0x4C, 0x71, 0x0D, 0x83, 0xF5}};

const wchar_t kExeName[] = L"SyncNotes.exe";
const wchar_t kDataFolder[] = L"MemilMemo";
const wchar_t kMarkerName[] = L"shell-menu.txt";

enum class Kind { NewNote, LinkNote, NewFolder, ClipNote };

std::wstring ModuleDir() {
    wchar_t path[MAX_PATH * 2] = {};
    DWORD n = GetModuleFileNameW(g_module, path, ARRAYSIZE(path));
    std::wstring dir(path, n);
    size_t slash = dir.find_last_of(L"\\/");
    return slash == std::wstring::npos ? L"" : dir.substr(0, slash);
}

std::wstring ExePath() { return ModuleDir() + L"\\" + kExeName; }

std::wstring MarkerPath() {
    PWSTR profile = nullptr;
    std::wstring out;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_Profile, 0, nullptr, &profile))) {
        out = std::wstring(profile) + L"\\" + kDataFolder + L"\\" + kMarkerName;
    }
    CoTaskMemFree(profile);
    return out;
}

// 앱이 적어 둔 글자 네 줄을 읽는다. 파일이 없으면 false(메뉴를 감춘다).
bool ReadTitles(std::wstring* new_note, std::wstring* link_note,
                std::wstring* new_folder = nullptr, std::wstring* clip_note = nullptr) {
    std::wstring path = MarkerPath();
    if (path.empty()) return false;
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ,
                              FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    char raw[1024] = {};
    DWORD got = 0;
    BOOL ok = ReadFile(file, raw, sizeof(raw) - 1, &got, nullptr);
    CloseHandle(file);
    if (!ok) return false;
    wchar_t wide[1024] = {};
    int n = MultiByteToWideChar(CP_UTF8, 0, raw, static_cast<int>(got), wide,
                                ARRAYSIZE(wide) - 1);
    std::wstring text(wide, n > 0 ? n : 0);
    if (!text.empty() && text[0] == 0xFEFF) text.erase(0, 1);
    std::vector<std::wstring> lines;
    size_t start = 0;
    while (start <= text.size() && lines.size() < 4) {
        size_t end = text.find(L'\n', start);
        if (end == std::wstring::npos) end = text.size();
        std::wstring line = text.substr(start, end - start);
        while (!line.empty() && (line.back() == L'\r' || line.back() == L' ')) {
            line.pop_back();
        }
        if (line.size() > 80) line.resize(80);
        lines.push_back(line);
        start = end + 1;
    }
    if (new_note) *new_note = lines.size() > 0 ? lines[0] : L"";
    if (link_note) *link_note = lines.size() > 1 ? lines[1] : L"";
    if (new_folder) *new_folder = lines.size() > 2 ? lines[2] : L"";
    if (clip_note) *clip_note = lines.size() > 3 ? lines[3] : L"";
    return true;
}

// 명령줄에 넣을 수 있게 따옴표로 감싼다. 끝의 역슬래시가 닫는 따옴표를 먹지
// 않게 두 배로 늘린다("C:\" 같은 경로).
std::wstring Quote(const std::wstring& value) {
    std::wstring out = L"\"";
    size_t slashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') {
            ++slashes;
        } else if (ch == L'"') {
            out.append(slashes * 2 + 1, L'\\');
            slashes = 0;
            out.push_back(ch);
            continue;
        } else {
            slashes = 0;
        }
        out.push_back(ch);
    }
    out.append(slashes, L'\\');
    out.push_back(L'"');
    return out;
}

HRESULT Launch(const std::wstring& arguments) {
    std::wstring exe = ExePath();
    std::wstring command = Quote(exe) + L" " + arguments;
    // Windows 의 명령줄 길이 제한(32767)을 넘기지 않는다.
    if (command.size() > 32000) command.resize(32000);
    std::vector<wchar_t> buffer(command.begin(), command.end());
    buffer.push_back(L'\0');
    STARTUPINFOW si = {sizeof(si)};
    PROCESS_INFORMATION pi = {};
    std::wstring dir = ModuleDir();
    if (!CreateProcessW(exe.c_str(), buffer.data(), nullptr, nullptr, FALSE, 0,
                        nullptr, dir.c_str(), &si, &pi)) {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return S_OK;
}

class Command final : public IExplorerCommand {
public:
    explicit Command(Kind kind) : kind_(kind) { InterlockedIncrement(&g_objects); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** out) override {
        if (!out) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IExplorerCommand) {
            *out = static_cast<IExplorerCommand*>(this);
            AddRef();
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&ref_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        long left = InterlockedDecrement(&ref_);
        if (left == 0) delete this;
        return left;
    }

    // IExplorerCommand
    IFACEMETHODIMP GetTitle(IShellItemArray*, PWSTR* title) override {
        if (!title) return E_POINTER;
        std::wstring new_note, link_note, new_folder, clip_note;
        ReadTitles(&new_note, &link_note, &new_folder, &clip_note);
        const std::wstring& text = kind_ == Kind::NewNote ? new_note
                                   : kind_ == Kind::NewFolder ? new_folder
                                   : kind_ == Kind::ClipNote ? clip_note
                                   : link_note;
        if (!text.empty()) return SHStrDupW(text.c_str(), title);
        return SHStrDupW(kind_ == Kind::NewNote ? L"Add New Note"
                         : kind_ == Kind::NewFolder ? L"Add New Note Folder"
                         : kind_ == Kind::ClipNote ? L"New Note from Clipboard"
                         : L"Link in a New Note",
                         title);
    }
    IFACEMETHODIMP GetIcon(IShellItemArray*, PWSTR* icon) override {
        if (!icon) return E_POINTER;
        return SHStrDupW((ExePath() + L",0").c_str(), icon);
    }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, PWSTR* tip) override {
        if (tip) *tip = nullptr;
        return E_NOTIMPL;
    }
    IFACEMETHODIMP GetCanonicalName(GUID* name) override {
        if (!name) return E_POINTER;
        *name = GUID_NULL;
        return S_OK;
    }
    IFACEMETHODIMP GetState(IShellItemArray*, BOOL, EXPCMDSTATE* state) override {
        if (!state) return E_POINTER;
        std::wstring new_folder, clip_note;
        bool shown = ReadTitles(nullptr, nullptr, &new_folder, &clip_note);
        // 옛 버전이 적어 둔 파일에는 폴더(셋째 줄)나 클립보드(넷째 줄) 항목의 글자가
        // 없다. 앱이 켜지며 고쳐 적을 때까지 그 항목은 감춘다(다른 언어로 보이지 않게).
        if (kind_ == Kind::NewFolder && new_folder.empty()) shown = false;
        if (kind_ == Kind::ClipNote && clip_note.empty()) shown = false;
        *state = shown ? ECS_ENABLED : ECS_HIDDEN;
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        if (kind_ == Kind::NewNote) return Launch(L"--new-note --at-cursor");
        if (kind_ == Kind::NewFolder) return Launch(L"--new-folder --at-cursor");
        if (kind_ == Kind::ClipNote) return Launch(L"--clip-note --at-cursor");
        std::wstring arguments = L"--link-note";
        DWORD count = 0;
        if (items) items->GetCount(&count);
        DWORD added = 0;
        for (DWORD i = 0; i < count; ++i) {
            IShellItem* item = nullptr;
            if (FAILED(items->GetItemAt(i, &item)) || !item) continue;
            PWSTR path = nullptr;
            if (SUCCEEDED(item->GetDisplayName(SIGDN_FILESYSPATH, &path)) && path) {
                std::wstring quoted = Quote(path);
                if (arguments.size() + quoted.size() + 1 < 31000) {
                    arguments += L" " + quoted;
                    ++added;
                }
            }
            CoTaskMemFree(path);
            item->Release();
        }
        if (!added) return S_OK;        // 파일 경로가 없는 항목(가상 폴더 등)
        return Launch(arguments);
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override {
        if (!flags) return E_POINTER;
        *flags = ECF_DEFAULT;
        return S_OK;
    }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** commands) override {
        if (commands) *commands = nullptr;
        return E_NOTIMPL;
    }

private:
    ~Command() { InterlockedDecrement(&g_objects); }

    long ref_ = 1;
    Kind kind_;
};

class Factory final : public IClassFactory {
public:
    explicit Factory(Kind kind) : kind_(kind) { InterlockedIncrement(&g_objects); }

    IFACEMETHODIMP QueryInterface(REFIID riid, void** out) override {
        if (!out) return E_POINTER;
        if (riid == IID_IUnknown || riid == IID_IClassFactory) {
            *out = static_cast<IClassFactory*>(this);
            AddRef();
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&ref_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        long left = InterlockedDecrement(&ref_);
        if (left == 0) delete this;
        return left;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        Command* command = new (std::nothrow) Command(kind_);
        if (!command) return E_OUTOFMEMORY;
        HRESULT hr = command->QueryInterface(riid, out);
        command->Release();
        return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) {
            InterlockedIncrement(&g_objects);
        } else {
            InterlockedDecrement(&g_objects);
        }
        return S_OK;
    }

private:
    ~Factory() { InterlockedDecrement(&g_objects); }

    long ref_ = 1;
    Kind kind_;
};

}  // namespace

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    Kind kind;
    if (clsid == CLSID_NewNote) {
        kind = Kind::NewNote;
    } else if (clsid == CLSID_LinkNote) {
        kind = Kind::LinkNote;
    } else if (clsid == CLSID_NewFolder) {
        kind = Kind::NewFolder;
    } else if (clsid == CLSID_ClipNote) {
        kind = Kind::ClipNote;
    } else {
        return CLASS_E_CLASSNOTAVAILABLE;
    }
    Factory* factory = new (std::nothrow) Factory(kind);
    if (!factory) return E_OUTOFMEMORY;
    HRESULT hr = factory->QueryInterface(riid, out);
    factory->Release();
    return hr;
}

STDAPI DllCanUnloadNow() { return g_objects == 0 ? S_OK : S_FALSE; }

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}
