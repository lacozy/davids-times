local ConfirmBox = require("ui/widget/confirmbox")
local DataStorage = require("datastorage")
local Dispatcher = require("dispatcher")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local NetworkMgr = require("ui/network/manager")
local ReaderUI = require("apps/reader/readerui")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local json = require("json")
local lfs = require("libs/libkoreader-lfs")
local logger = require("logger")
local ltn12 = require("ltn12")
local socket = require("socket")
local http = require("socket.http")
local https = require("ssl.https")
local socketutil = require("socketutil")
local util = require("util")
local _ = require("gettext")

local SETTING_BASE_URL = "davidtimes_base_url"
local SETTING_AUTO_PULL = "davidtimes_auto_pull"
local SETTING_LAST_DATE = "davidtimes_last_downloaded_date"
local DEFAULT_BASE_URL = "https://lacozy.github.io/davids-times"
local DEFAULT_SAVE_DIR = "/mnt/us/documents/DavidTimes"

local DavidTimes = WidgetContainer:extend{
    name = "davidtimes",
    is_doc_only = false,
}

function DavidTimes:init()
    self.ui.menu:registerToMainMenu(self)
    self:onDispatcherRegisterActions()
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
    -- Prefer Kindle documents folder if present, otherwise KOReader home dir
    if lfs.attributes("/mnt/us/documents", "mode") == "directory" then
        util.makePath(DEFAULT_SAVE_DIR)
        return DEFAULT_SAVE_DIR
    end
    local fallback = DataStorage:getDataDir() .. "/DavidTimes"
    util.makePath(fallback)
    return fallback
end

function DavidTimes:getBaseUrl()
    local url = G_reader_settings:readSetting(SETTING_BASE_URL)
    if not url or url == "" then
        url = DEFAULT_BASE_URL
    end
    url = url:gsub("%s+", ""):gsub("/+$", "")
    return url
end

function DavidTimes:httpGet(url, sink_fn, redirects)
    redirects = redirects or 0
    if redirects > 5 then
        return nil, "Too many redirects"
    end

    local is_https = url:sub(1, 8) == "https://"
    local req_fn = is_https and https.request or http.request
    socketutil:set_timeout(socketutil.LARGE_BLOCK_TIMEOUT, socketutil.LARGE_TOTAL_TIMEOUT)

    local ok, code, headers = req_fn{
        url = url,
        method = "GET",
        sink = sink_fn,
        headers = {
            ["User-Agent"] = "KOReader-DavidTimes/1.0",
        },
    }
    socketutil:reset_timeout()

    if not ok then
        return nil, tostring(code or "network error")
    end

    code = tonumber(code)
    if code and (code == 301 or code == 302 or code == 303 or code == 307 or code == 308) then
        local loc = headers and (headers["location"] or headers["Location"])
        if loc then
            return self:httpGet(loc, sink_fn, redirects + 1)
        end
    end

    if code ~= 200 then
        return nil, "HTTP " .. tostring(code)
    end
    return true
end

function DavidTimes:fetchJson(url)
    local chunks = {}
    local ok, err = self:httpGet(url, ltn12.sink.table(chunks))
    if not ok then
        return nil, err
    end
    local raw = table.concat(chunks)
    local parsed_ok, data = pcall(json.decode, raw)
    if not parsed_ok or type(data) ~= "table" then
        return nil, "Invalid latest.json response"
    end
    return data
end

function DavidTimes:downloadToFile(url, dest_path)
    local tmp_path = dest_path .. ".tmp"
    local fh, open_err = io.open(tmp_path, "wb")
    if not fh then
        return nil, open_err
    end

    local ok, err = self:httpGet(url, ltn12.sink.file(fh))
    if not ok then
        os.remove(tmp_path)
        return nil, err
    end

    os.remove(dest_path)
    local renamed, ren_err = os.rename(tmp_path, dest_path)
    if not renamed then
        os.remove(tmp_path)
        return nil, ren_err
    end
    return true
end

function DavidTimes:promptForBaseUrl(on_saved)
    local dialog
    dialog = InputDialog:new{
        title = _("The David Times — Base URL"),
        input = self:getBaseUrl(),
        input_hint = "https://YOUR-USERNAME.github.io/davids-times",
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
end

function DavidTimes:fetchLatest(open_after, silent)
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
        local notice
        if not silent then
            notice = InfoMessage:new{
                text = _("Checking for today’s edition of The David Times…"),
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
            G_reader_settings:saveSetting(SETTING_LAST_DATE, meta.date_iso or "")
        end

        if notice then UIManager:close(notice) end

        if open_after then
            ReaderUI:showReader(dest_path)
        elseif not already_exists then
            UIManager:show(InfoMessage:new{
                text = _("The David Times is ready!\n") .. (meta.date_display or filename),
                timeout = 4,
            })
        end
    end

    if silent then
        if NetworkMgr:isConnected() then
            do_fetch()
        end
    else
        NetworkMgr:runWhenOnline(do_fetch)
    end
end

function DavidTimes:onDavidTimesFetchToday()
    self:fetchLatest(true, false)
    return true
end

function DavidTimes:onNetworkConnected()
    if not G_reader_settings:isTrue(SETTING_AUTO_PULL) then
        return
    end
    local today = os.date("%Y-%m-%d")
    if G_reader_settings:readSetting(SETTING_LAST_DATE) == today then
        return
    end
    -- Wait 3 seconds for DNS/TLS to settle after Wi-Fi wakes up
    UIManager:scheduleIn(3, function()
        self:fetchLatest(false, true)
    end)
end

function DavidTimes:addToMainMenu(menu_items)
    menu_items.davidtimes = {
        text = _("The David Times"),
        sorting_hint = "tools",
        sub_item_table = {
            {
                text = _("Download & open today’s issue"),
                callback = function()
                    self:fetchLatest(true, false)
                end,
            },
            {
                text = _("Auto-download when Wi-Fi connects"),
                checked_func = function()
                    return G_reader_settings:isTrue(SETTING_AUTO_PULL)
                end,
                callback = function()
                    G_reader_settings:toggle(SETTING_AUTO_PULL)
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
