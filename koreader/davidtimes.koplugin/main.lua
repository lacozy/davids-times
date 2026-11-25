local DataStorage = require("datastorage")
local Dispatcher = require("dispatcher")
local Event = require("ui/event")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local JSON = require("json")
local NetworkMgr = require("ui/network/manager")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local http = require("socket.http")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local ltn12 = require("ltn12")
local socket = require("socket")
local socketutil = require("socketutil")
local util = require("util")
local _ = require("gettext")

local SETTING_BASE_URL = "davidtimes_base_url"
local SETTING_SAVE_DIR = "davidtimes_save_dir"
local SETTING_AUTO_PULL = "davidtimes_auto_pull"
local SETTING_FORCE_DAY = "davidtimes_force_day_mode"
local SETTING_LAST_DATE = "davidtimes_last_downloaded_date"
local DEFAULT_BASE_URL = "https://lacozy.github.io/davids-times"
local DEFAULT_SAVE_DIR = "/mnt/us/documents/DavidTimes"
local MAX_LOCAL_ISSUES = 14

local DavidTimes = WidgetContainer:extend{
    name = "davidtimes",
    is_doc_only = false,
}

local function safeRun(label, fn, show_error)
    local ok, err = xpcall(fn, function(e)
        return tostring(e)
    end)
    if not ok then
        logger.err("DavidTimes error in " .. label .. ":", err)
        if show_error then
            UIManager:show(InfoMessage:new{
                text = _("The David Times error (" .. label .. "):\n") .. tostring(err),
            })
        end
    end
    return ok, err
end

function DavidTimes:init()
    safeRun("init", function()
        if G_reader_settings:readSetting(SETTING_FORCE_DAY) == nil then
            G_reader_settings:saveSetting(SETTING_FORCE_DAY, true)
        end
        if self.ui and self.ui.menu then
            self.ui.menu:registerToMainMenu(self)
        end
        self:onDispatcherRegisterActions()
        self:scheduleAutoPull()
    end, false)
end

function DavidTimes:onDispatcherRegisterActions()
    Dispatcher:registerAction("davidtimes_fetch_today", {
        category = "none",
        event = "DavidTimesFetchToday",
        title = _("Fetch & Open The David Times"),
        general = true,
    })
end

function DavidTimes:getSaveDir()
    local custom = G_reader_settings:readSetting(SETTING_SAVE_DIR)
    if custom and custom ~= "" then
        util.makePath(custom)
        return custom
    end
    if lfs.attributes("/mnt/us/documents", "mode") == "directory" then
        util.makePath(DEFAULT_SAVE_DIR)
        return DEFAULT_SAVE_DIR
    end
    local fallback = DataStorage:getDataDir() .. "/DavidTimes"
    util.makePath(fallback)
    return fallback
end

function DavidTimes:chooseSaveDir()
    safeRun("chooseSaveDir", function()
        local PathChooser = require("ui/widget/pathchooser")
        local chooser = PathChooser:new{
            select_directory = true,
            select_file = false,
            show_files = false,
            path = self:getSaveDir(),
            onConfirm = function(dir_path)
                if dir_path and dir_path ~= "" then
                    dir_path = dir_path:gsub("/+$", "")
                    G_reader_settings:saveSetting(SETTING_SAVE_DIR, dir_path)
                    UIManager:show(InfoMessage:new{
                        text = _("Download folder set to:\n") .. dir_path,
                        timeout = 3,
                    })
                end
            end,
        }
        UIManager:show(chooser)
    end, true)
end

function DavidTimes:getBaseUrl()
    local url = G_reader_settings:readSetting(SETTING_BASE_URL)
    if not url or url == "" then
        url = DEFAULT_BASE_URL
    end
    url = url:gsub("%s+", ""):gsub("/+$", "")
    return url
end

function DavidTimes:ensureDayModeIfEnabled()
    if not G_reader_settings:isTrue(SETTING_FORCE_DAY) then
        return
    end
    if G_reader_settings:isTrue("night_mode") then
        UIManager:broadcastEvent(Event:new("ToggleNightMode"))
    end
end

function DavidTimes:onReaderReady()
    safeRun("onReaderReady", function()
        local doc_path = self.ui and self.ui.document and self.ui.document.file or ""
        if doc_path:find("David%-Times") or doc_path:find("DavidTimes") or doc_path:find("latest%.pdf") then
            UIManager:nextTick(function()
                safeRun("dayMode", function() self:ensureDayModeIfEnabled() end, false)
            end)
        end
    end, false)
end

function DavidTimes:pruneOldIssues()
    safeRun("prune", function()
        local save_dir = self:getSaveDir()
        local files = {}
        for entry in lfs.dir(save_dir) do
            if entry:match("%.pdf$") then
                table.insert(files, entry)
            end
        end
        table.sort(files, function(a, b) return a > b end)
        for i = MAX_LOCAL_ISSUES + 1, #files do
            os.remove(save_dir .. "/" .. files[i])
        end
    end, false)
end

function DavidTimes:httpRequest(url, dest_file_path, redirects)
    redirects = redirects or 0
    if redirects > 5 then
        return nil, "Too many redirects"
    end

    local chunks = nil
    local sink = nil
    if dest_file_path then
        local fh, open_err = io.open(dest_file_path, "wb")
        if not fh then
            return nil, "Cannot write file: " .. tostring(open_err)
        end
        sink = ltn12.sink.file(fh)
    else
        chunks = {}
        sink = ltn12.sink.table(chunks)
    end

    local req_fn = http.request
    if url:sub(1, 8) == "https://" then
        local ok_ssl, https_mod = pcall(require, "ssl.https")
        if ok_ssl and https_mod and https_mod.request then
            req_fn = https_mod.request
        end
    end

    local b_to = dest_file_path and (socketutil.FILE_BLOCK_TIMEOUT or 15) or (socketutil.LARGE_BLOCK_TIMEOUT or 10)
    local t_to = dest_file_path and (socketutil.FILE_TOTAL_TIMEOUT or 60) or (socketutil.LARGE_TOTAL_TIMEOUT or 30)
    socketutil:set_timeout(b_to, t_to)

    local ok_req, code, headers, status = pcall(function()
        return socket.skip(1, req_fn{
            url = url,
            method = "GET",
            sink = sink,
            headers = {
                ["Accept-Encoding"] = "identity",
                ["User-Agent"] = "KOReader-DavidTimes/1.3",
            },
        })
    end)
    socketutil:reset_timeout()

    if not ok_req then
        return nil, "Request error: " .. tostring(code)
    end

    local num_code = tonumber(code)
    if not num_code then
        return nil, tostring(code or status or "network error")
    end

    if num_code == 301 or num_code == 302 or num_code == 303 or num_code == 307 or num_code == 308 then
        local loc = headers and (headers["location"] or headers["Location"])
        if loc then
            -- Re-open fresh sink on redirect so we never write to a closed file handle
            return self:httpRequest(loc, dest_file_path, redirects + 1)
        end
    end

    if num_code ~= 200 then
        return nil, "HTTP " .. tostring(num_code)
    end

    if chunks then
        return table.concat(chunks)
    end
    return true
end

function DavidTimes:fetchJson(url)
    local raw, err = self:httpRequest(url, nil, 0)
    if not raw then
        return nil, err
    end
    local parsed_ok, data = pcall(JSON.decode, raw)
    if not parsed_ok or type(data) ~= "table" then
        return nil, "Invalid latest.json response"
    end
    return data
end

function DavidTimes:downloadToFile(url, dest_path)
    local tmp_path = dest_path .. ".tmp"
    local ok, err = self:httpRequest(url, tmp_path, 0)
    if not ok then
        os.remove(tmp_path)
        return nil, err
    end

    local attr = lfs.attributes(tmp_path)
    if not attr or (attr.size or 0) < 1024 then
        os.remove(tmp_path)
        return nil, "Downloaded file was empty or incomplete"
    end

    os.remove(dest_path)
    local renamed, ren_err = os.rename(tmp_path, dest_path)
    if not renamed then
        os.remove(tmp_path)
        return nil, "Save failed: " .. tostring(ren_err)
    end
    return true
end

function DavidTimes:openDocument(dest_path)
    -- Defer to nextTick so any open TouchMenu/InfoMessage closes cleanly before ReaderUI opens
    UIManager:nextTick(function()
        safeRun("openDocument", function()
            local ReaderUI = require("apps/reader/readerui")
            ReaderUI:showReader(dest_path)
        end, true)
    end)
end

function DavidTimes:promptForBaseUrl(on_saved)
    safeRun("promptForBaseUrl", function()
        local dialog
        dialog = InputDialog:new{
            title = _("The David Times — Base URL"),
            input = self:getBaseUrl(),
            input_hint = DEFAULT_BASE_URL,
            description = _("Enter your GitHub Pages URL (without trailing slash)."),
            buttons = {
                {
                    {
                        text = _("Cancel"),
                        callback = function()
                            UIManager:close(dialog)
                        end,
                    },
                    {
                        text = _("Save"),
                        is_enter_default = true,
                        callback = function()
                            local val = (dialog:getInputText() or ""):gsub("%s+", ""):gsub("/+$", "")
                            G_reader_settings:saveSetting(SETTING_BASE_URL, val)
                            UIManager:close(dialog)
                            if on_saved and val ~= "" then
                                on_saved()
                            end
                        end,
                    },
                },
            },
        }
        UIManager:show(dialog)
        dialog:onShowKeyboard()
    end, true)
end

function DavidTimes:fetchLatest(open_after, silent)
    safeRun("fetchLatest", function()
        local base_url = self:getBaseUrl()
        if base_url == "" then
            if not silent then
                self:promptForBaseUrl(function()
                    self:fetchLatest(open_after, false)
                end)
            end
            return
        end

        local do_fetch = function()
            safeRun("do_fetch", function()
                local notice
                if not silent then
                    notice = InfoMessage:new{
                        text = _("Downloading today’s edition of The David Times…"),
                    }
                    UIManager:show(notice)
                    UIManager:forceRePaint()
                end

                local meta, err = self:fetchJson(base_url .. "/latest.json")
                if not meta then
                    if notice then UIManager:close(notice) end
                    if not silent then
                        UIManager:show(InfoMessage:new{
                            text = _("Could not fetch latest.json:\n") .. tostring(err),
                        })
                    end
                    logger.warn("DavidTimes: latest.json error:", err)
                    return
                end

                local filename = meta.filename or ((meta.date_iso or os.date("%Y-%m-%d")) .. "-The-David-Times.pdf")
                local dest_path = self:getSaveDir() .. "/" .. filename
                local already_exists = lfs.attributes(dest_path, "mode") == "file"

                if not already_exists then
                    local pdf_url = meta.pdf_url or (base_url .. "/latest.pdf")
                    local ok, dl_err = self:downloadToFile(pdf_url, dest_path)
                    if not ok then
                        if notice then UIManager:close(notice) end
                        if not silent then
                            UIManager:show(InfoMessage:new{
                                text = _("Download failed:\n") .. tostring(dl_err),
                            })
                        end
                        logger.warn("DavidTimes: PDF download error:", dl_err)
                        return
                    end
                    G_reader_settings:saveSetting(SETTING_LAST_DATE, meta.date_iso or os.date("%Y-%m-%d"))
                    self:pruneOldIssues()
                    safeRun("refreshFM", function()
                        local FileManager = require("apps/filemanager/filemanager")
                        if FileManager.instance then
                            FileManager.instance:onRefresh()
                        end
                    end, false)
                end

                if notice then UIManager:close(notice) end

                if open_after then
                    self:openDocument(dest_path)
                elseif not already_exists then
                    UIManager:show(InfoMessage:new{
                        text = _("The David Times is ready!\n") .. (meta.date_display or filename),
                        timeout = 4,
                    })
                end
            end, not silent)
        end

        if silent then
            if NetworkMgr:isConnected() then
                do_fetch()
            end
        else
            NetworkMgr:runWhenOnline(do_fetch)
        end
    end, not silent)
end

function DavidTimes:scheduleAutoPull()
    if not G_reader_settings:isTrue(SETTING_AUTO_PULL) then
        return
    end
    local today = os.date("%Y-%m-%d")
    if G_reader_settings:readSetting(SETTING_LAST_DATE) == today then
        return
    end
    UIManager:scheduleIn(4, function()
        safeRun("autoPull", function()
            if NetworkMgr:isConnected() then
                self:fetchLatest(false, true)
            end
        end, false)
    end)
end

function DavidTimes:onDavidTimesFetchToday()
    UIManager:nextTick(function()
        self:fetchLatest(true, false)
    end)
    return true
end

function DavidTimes:onNetworkConnected()
    safeRun("onNetworkConnected", function() self:scheduleAutoPull() end, false)
end

function DavidTimes:onResume()
    safeRun("onResume", function() self:scheduleAutoPull() end, false)
end

function DavidTimes:addToMainMenu(menu_items)
    menu_items.davidtimes = {
        text = _("The David Times"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("Download & open today’s issue"),
                callback = function()
                    UIManager:nextTick(function()
                        self:fetchLatest(true, false)
                    end)
                end,
            },
            {
                text = _("Auto-download when Kindle wakes on Wi-Fi"),
                checked_func = function()
                    return G_reader_settings:isTrue(SETTING_AUTO_PULL)
                end,
                callback = function()
                    G_reader_settings:toggle(SETTING_AUTO_PULL)
                end,
            },
            {
                text = _("Always open in Day Mode (disable Night Mode)"),
                checked_func = function()
                    return G_reader_settings:isTrue(SETTING_FORCE_DAY)
                end,
                callback = function()
                    G_reader_settings:toggle(SETTING_FORCE_DAY)
                end,
            },
            {
                text = _("Set download folder…"),
                callback = function()
                    self:chooseSaveDir()
                end,
            },
            {
                text = _("Set newspaper URL…"),
                callback = function()
                    self:promptForBaseUrl()
                end,
            },
            {
                text = _("Show save folder"),
                callback = function()
                    UIManager:show(InfoMessage:new{
                        text = _("Newspapers are saved in:\n") .. self:getSaveDir(),
                    })
                end,
            },
        },
    }
end

return DavidTimes
