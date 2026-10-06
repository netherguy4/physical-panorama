use cosmic::iced::core::text::{Ellipsize, EllipsizeHeightLimit};
use cosmic::iced::platform_specific::shell::wayland::commands::popup::destroy_popup;
use cosmic::iced::{Length, Subscription, window};
use cosmic::{Element, Task, app, widget};
use serde::Deserialize;
use std::{collections::VecDeque, path::PathBuf, time::Duration};

const ICON: &str = "preferences-desktop-wallpaper-symbolic";
const INTERVALS: [u32; 4] = [5, 15, 30, 60];

#[derive(Clone, Debug, Default, Deserialize, PartialEq)]
struct Group {
    id: String,
    count: u32,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
struct Status {
    title: Option<String>,
    active: bool,
    slideshow: bool,
    #[serde(default = "default_interval")]
    interval: u32,
    #[serde(default = "default_group")]
    group: String,
    #[serde(default)]
    groups: Vec<Group>,
    eligible: u32,
    can_previous: bool,
}

fn default_interval() -> u32 {
    15
}
fn default_group() -> String {
    "all".to_owned()
}

fn group_label(id: &str) -> &str {
    if id == "all" { "Все" } else { id }
}

/// Dropdown values: presets plus the configured interval when it is not one of them.
fn intervals(current: u32) -> Vec<u32> {
    let mut values = INTERVALS.to_vec();
    if !values.contains(&current) {
        values.push(current);
        values.sort_unstable();
    }
    values
}

#[derive(Clone, Debug, PartialEq)]
enum Operation {
    Read,
    Next,
    Previous,
    Pause,
    Start(u32),
    Group(String),
    Interval(u32),
}

impl Operation {
    fn args(&self) -> Vec<String> {
        let args: &[&str] = match self {
            Self::Read => &["status", "--json"],
            Self::Next => &["next"],
            Self::Previous => &["previous"],
            Self::Pause => &["static"],
            Self::Start(n) => return vec!["slideshow".into(), "--interval".into(), n.to_string()],
            Self::Group(id) => return vec!["configure".into(), "--group".into(), id.clone()],
            Self::Interval(n) => {
                return vec!["configure".into(), "--interval".into(), n.to_string()];
            }
        };
        args.iter().map(|arg| (*arg).to_owned()).collect()
    }
}

async fn call(args: Vec<String>, seconds: u64) -> Result<String, String> {
    let cli = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_default()
        .join(".local/bin/physical-panorama");
    let mut command = tokio::process::Command::new(cli);
    command.args(&args).kill_on_drop(true);
    let result = tokio::time::timeout(Duration::from_secs(seconds), command.output())
        .await
        .map_err(|_| {
            format!(
                "physical-panorama {} не ответил за {seconds}\u{a0}с.",
                args[0]
            )
        })?
        .map_err(|error| format!("Не удалось запустить physical-panorama: {error}"))?;
    if !result.status.success() {
        let stderr = String::from_utf8_lossy(&result.stderr);
        let reason = stderr.lines().rev().find(|line| !line.trim().is_empty());
        return Err(reason
            .unwrap_or("команда завершилась с ошибкой")
            .trim()
            .to_owned());
    }
    Ok(String::from_utf8_lossy(&result.stdout).into_owned())
}

/// Runs the action (if any), then reads status, sequentially inside one task.
async fn run(operation: Operation) -> Result<Status, String> {
    if operation != Operation::Read {
        // Actions may render a missing cache for every monitor.
        call(operation.args(), 120).await?;
    }
    let json = call(Operation::Read.args(), 10).await?;
    serde_json::from_str(&json).map_err(|error| format!("Непонятный ответ status --json: {error}"))
}

struct App {
    core: app::Core,
    status: Option<Status>,
    busy: Option<Operation>,
    pending: VecDeque<Operation>,
    error: Option<String>,
    failed_action: Option<Operation>,
    popup: Option<window::Id>,
}

#[derive(Debug, Clone)]
enum Message {
    Poll,
    Run(Operation),
    Finished(Result<Status, String>),
    TogglePopup,
    CloseRequested(window::Id),
    Surface(cosmic::surface::Action),
}

impl App {
    fn new(core: app::Core) -> Self {
        Self {
            core,
            status: None,
            busy: None,
            pending: VecDeque::new(),
            error: None,
            failed_action: None,
            popup: None,
        }
    }
    fn switching(&self) -> bool {
        self.busy.as_ref().is_some_and(|op| *op != Operation::Read) || !self.pending.is_empty()
    }
    fn execute(&mut self, operation: Operation) -> app::Task<Message> {
        if self.busy.is_some() {
            // Group and interval choices are independent; neither may overwrite the other.
            if operation != Operation::Read {
                self.pending.push_back(operation);
            }
            return Task::none();
        }
        if operation != Operation::Read {
            self.error = None;
            self.failed_action = None;
        }
        self.busy = Some(operation.clone());
        Task::perform(run(operation), |result| {
            cosmic::Action::App(Message::Finished(result))
        })
    }
    fn popup_task(&mut self) -> app::Task<Message> {
        cosmic::surface::surface_task(cosmic::surface::action::app_popup(
            |_| Default::default(),
            |app: &mut Self| {
                let id = window::Id::unique();
                app.popup = Some(id);
                app.core.applet.get_popup_settings(
                    app.core.main_window_id().unwrap(),
                    id,
                    None,
                    None,
                    None,
                )
            },
            None,
        ))
    }
    fn content(&self) -> Element<'_, Message> {
        let mut content = widget::column(vec![])
            .spacing(12)
            .padding(16)
            .width(Length::Fill);
        let Some(status) = &self.status else {
            let text = match &self.error {
                Some(error) => format!("Не удалось прочитать состояние: {error}"),
                None => "Читаю состояние панорамы…".to_owned(),
            };
            content = content
                .push(widget::text::title4("Физическая панорама"))
                .push(widget::text(text));
            if self.error.is_some() && self.busy.is_none() {
                content = content.push(
                    widget::button::standard("Повторить").on_press(Message::Run(Operation::Read)),
                );
            }
            return content.into();
        };
        let idle = !self.switching();
        let title = match (&status.title, status.active) {
            (Some(title), true) => title.as_str(),
            (_, false) => "Панорама не применена",
            (None, true) => "Изображение без названия",
        };
        let mode = if status.slideshow {
            format!("Слайд-шоу: каждые {}\u{a0}мин", status.interval)
        } else {
            "Слайд-шоу на паузе".to_owned()
        };
        content = content.push(
            widget::column(vec![])
                .spacing(4)
                .push(
                    widget::text::title4(title)
                        .ellipsize(Ellipsize::End(EllipsizeHeightLimit::Lines(2))),
                )
                .push(widget::text::caption(format!(
                    "Группа «{}» · подходящих: {}",
                    group_label(&status.group),
                    status.eligible
                )))
                .push(widget::text::caption(mode)),
        );
        let has_images = status.eligible > 0;
        let button = |label, icon, operation, enabled: bool| {
            widget::button::standard(label)
                .leading_icon(widget::icon::from_name(icon))
                .width(Length::Fill)
                .on_press_maybe((idle && enabled).then_some(Message::Run(operation)))
        };
        let (toggle_label, toggle_icon, toggle) = if status.slideshow {
            ("Пауза", "media-playback-pause-symbolic", Operation::Pause)
        } else {
            (
                "Запустить",
                "media-playback-start-symbolic",
                Operation::Start(status.interval),
            )
        };
        content = content.push(
            widget::row(vec![])
                .spacing(8)
                .width(Length::Fill)
                .push(button(
                    "Назад",
                    "media-skip-backward-symbolic",
                    Operation::Previous,
                    status.can_previous,
                ))
                .push(button(
                    toggle_label,
                    toggle_icon,
                    toggle,
                    has_images || status.slideshow,
                ))
                .push(button(
                    "Далее",
                    "media-skip-forward-symbolic",
                    Operation::Next,
                    has_images,
                )),
        );
        if !has_images {
            content = content.push(widget::text(
                "В этой группе нет изображений для текущей раскладки мониторов. Выберите другую группу.",
            ));
        }
        let parent = self.popup.unwrap_or(window::Id::NONE);
        let mut groups = status.groups.clone();
        if !groups.iter().any(|group| group.id == status.group) {
            groups.push(Group {
                id: status.group.clone(),
                count: 0,
            });
        }
        let group_labels: Vec<String> = groups
            .iter()
            .map(|group| format!("{} ({})", group_label(&group.id), group.count))
            .collect();
        let selected_group = groups.iter().position(|group| group.id == status.group);
        let ids: Vec<String> = groups.into_iter().map(|group| group.id).collect();
        let minutes = intervals(status.interval);
        let interval_labels: Vec<String> =
            minutes.iter().map(|n| format!("{n}\u{a0}мин")).collect();
        let selected_interval = minutes.iter().position(|n| *n == status.interval);
        content = content
            .push(widget::divider::horizontal::default())
            .push(widget::settings::item(
                "Группа",
                widget::dropdown::popup_dropdown(
                    group_labels,
                    selected_group,
                    move |i| Message::Run(Operation::Group(ids[i].clone())),
                    parent,
                    Message::Surface,
                    |message| message,
                ),
            ))
            .push(widget::settings::item(
                "Интервал",
                widget::dropdown::popup_dropdown(
                    interval_labels,
                    selected_interval,
                    move |i| Message::Run(Operation::Interval(minutes[i])),
                    parent,
                    Message::Surface,
                    |message| message,
                ),
            ));
        if self.switching() {
            content = content.push(widget::text::caption(
                "Выполняю… Подготовка нового изображения может занять до двух минут.",
            ));
        } else if let Some(error) = &self.error {
            content = content.push(widget::text(format!("Ошибка: {error}"))).push(
                widget::button::standard("Повторить").on_press(Message::Run(
                    self.failed_action.clone().unwrap_or(Operation::Read),
                )),
            );
        }
        content.into()
    }
}

impl cosmic::Application for App {
    type Executor = cosmic::SingleThreadExecutor;
    type Flags = ();
    type Message = Message;
    const APP_ID: &'static str = "io.github.netherguy4.PhysicalPanorama";
    fn core(&self) -> &app::Core {
        &self.core
    }
    fn core_mut(&mut self) -> &mut app::Core {
        &mut self.core
    }
    fn init(core: app::Core, _: ()) -> (Self, app::Task<Message>) {
        let mut app = Self::new(core);
        let task = app.execute(Operation::Read);
        (app, task)
    }
    fn style(&self) -> Option<cosmic::iced::theme::Style> {
        Some(cosmic::applet::style())
    }
    fn subscription(&self) -> Subscription<Message> {
        // Slideshow changes happen in a systemd timer; refresh only while someone is looking.
        if self.popup.is_some() {
            cosmic::iced::time::every(Duration::from_secs(5)).map(|_| Message::Poll)
        } else {
            Subscription::none()
        }
    }
    fn update(&mut self, message: Message) -> app::Task<Message> {
        match message {
            Message::Poll => return self.execute(Operation::Read),
            Message::Run(operation) => return self.execute(operation),
            Message::Finished(result) => {
                let operation = self.busy.take();
                match result {
                    Ok(status) => {
                        self.status = Some(status);
                        if self.failed_action.is_none() {
                            self.error = None;
                        }
                    }
                    Err(error) => {
                        if self.failed_action.is_none() || operation != Some(Operation::Read) {
                            self.error = Some(error);
                            self.failed_action = operation.filter(|op| *op != Operation::Read);
                        }
                    }
                }
                if let Some(pending) = self.pending.pop_front() {
                    return self.execute(pending);
                }
            }
            Message::TogglePopup => {
                if let Some(id) = self.popup.take() {
                    return destroy_popup(id);
                }
                let popup = self.popup_task();
                let refresh = self.execute(Operation::Read);
                return Task::batch([popup, refresh]);
            }
            Message::CloseRequested(id) => {
                if self.popup == Some(id) {
                    self.popup = None;
                }
            }
            Message::Surface(action) => {
                return cosmic::task::message(cosmic::Action::Cosmic(
                    cosmic::app::Action::Surface(action),
                ));
            }
        }
        Task::none()
    }
    fn view(&self) -> Element<'_, Message> {
        self.core
            .applet
            .icon_button(ICON)
            .on_press(Message::TogglePopup)
            .description("Физическая панорама: открыть управление обоями")
            .into()
    }
    fn view_window(&self, _: window::Id) -> Element<'_, Message> {
        self.core
            .applet
            .popup_container(widget::container(self.content()).width(360))
            .into()
    }
    fn on_close_requested(&self, id: window::Id) -> Option<Message> {
        Some(Message::CloseRequested(id))
    }
}

fn main() -> cosmic::iced::Result {
    cosmic::applet::run::<App>(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use cosmic::Application;

    const SAMPLE: &str = r#"{"panorama":"p1","title":"Горы","active":true,"slideshow":true,
        "interval":20,"group":"all","groups":[{"id":"all","count":12},{"id":"nature","count":5}],
        "eligible":12,"can_previous":false}"#;

    #[test]
    fn parses_status_and_queues_user_command_behind_background_read() {
        let status: Status = serde_json::from_str(SAMPLE).unwrap();
        assert_eq!(
            status.groups[1],
            Group {
                id: "nature".into(),
                count: 5
            }
        );
        assert_eq!(intervals(status.interval), [5, 15, 20, 30, 60]);
        assert_eq!(
            Operation::Start(20).args(),
            ["slideshow", "--interval", "20"]
        );

        let mut app = App::new(app::Core::default());
        let _ = app.execute(Operation::Read);
        assert!(!app.switching());
        let _ = app.update(Message::Run(Operation::Group("nature".into())));
        assert!(app.switching());
        assert_eq!(app.busy, Some(Operation::Read));
        let _ = app.update(Message::Run(Operation::Interval(30)));
        let _ = app.update(Message::Finished(Ok(status.clone())));
        assert_eq!(app.busy, Some(Operation::Group("nature".into())));
        assert_eq!(app.pending.front(), Some(&Operation::Interval(30)));
        let _ = app.update(Message::Finished(Ok(status.clone())));
        assert_eq!(app.busy, Some(Operation::Interval(30)));
        assert!(app.pending.is_empty());
        let _ = app.update(Message::Finished(Err("нет кэша".into())));
        assert!(!app.switching());
        assert_eq!(app.status, Some(status.clone()));
        assert_eq!(app.error.as_deref(), Some("нет кэша"));
        let _ = app.update(Message::Poll);
        let _ = app.update(Message::Finished(Ok(status.clone())));
        assert_eq!(app.error.as_deref(), Some("нет кэша"));
        assert_eq!(app.failed_action, Some(Operation::Interval(30)));
        let _ = app.update(Message::Run(Operation::Interval(30)));
        let _ = app.update(Message::Finished(Ok(status)));
        assert!(app.error.is_none() && app.failed_action.is_none());
    }

    /// Draws on the tiny-skia renderer at 2x without a display server or window.
    fn snapshot(element: Element<'_, Message>, theme: &cosmic::Theme, path: &std::path::Path) {
        use cosmic::iced::advanced::renderer::{Headless, Style};
        use cosmic::iced::advanced::{clipboard, mouse};
        use cosmic::iced::runtime::user_interface::{Cache, UserInterface};
        use cosmic::iced::{Event, Size, theme::Base};
        let size = Size::new(360.0, 340.0);
        let mut renderer = cosmic::Renderer::new(cosmic::font::default(), 14.into());
        let mut ui = UserInterface::build(element, size, Cache::default(), &mut renderer);
        let redraw = Event::Window(window::Event::RedrawRequested(std::time::Instant::now()));
        let _ = ui.update(
            &[redraw],
            mouse::Cursor::Unavailable,
            &mut renderer,
            &mut clipboard::Null,
            &mut vec![],
        );
        let base = theme.base();
        let style = Style {
            icon_color: theme.cosmic().on_bg_color().into(),
            text_color: base.text_color,
            scale_factor: 2.0,
        };
        ui.draw(&mut renderer, theme, &style, mouse::Cursor::Unavailable);
        let rgba = renderer.screenshot(Size::new(720, 680), 2.0, base.background_color);
        let mut encoder = png::Encoder::new(std::fs::File::create(path).unwrap(), 720, 680);
        encoder.set_color(png::ColorType::Rgba);
        encoder
            .write_header()
            .unwrap()
            .write_image_data(&rgba)
            .unwrap();
    }

    /// Renders review screenshots headlessly: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn snapshots() {
        let dir = std::path::Path::new("/tmp/physical-panorama-review");
        std::fs::create_dir_all(dir).unwrap();
        let normal: Status = serde_json::from_str(SAMPLE).unwrap();
        let long = Status {
            title: Some("Очень длинное название панорамы: рассвет над заснеженными пиками Кавказского хребта".into()),
            slideshow: false,
            ..normal.clone()
        };
        let empty = Status {
            eligible: 0,
            group: "nature".into(),
            can_previous: false,
            groups: vec![
                Group {
                    id: "all".into(),
                    count: 7,
                },
                Group {
                    id: "nature".into(),
                    count: 0,
                },
            ],
            ..normal.clone()
        };
        let cases: [(&str, Option<Status>, Option<Operation>, Option<&str>); 5] = [
            ("normal", Some(normal.clone()), None, None),
            ("busy-long-title", Some(long), Some(Operation::Next), None),
            (
                "error",
                Some(normal),
                None,
                Some("physical-panorama next не ответил за 120\u{a0}с."),
            ),
            ("empty", Some(empty), None, None),
            ("loading", None, Some(Operation::Read), None),
        ];
        for (name, status, busy, error) in cases {
            for (theme_name, theme) in [
                ("dark", cosmic::Theme::dark()),
                ("light", cosmic::Theme::light()),
            ] {
                let mut app = App::new(app::Core::default());
                app.status = status.clone();
                app.busy = busy.clone();
                app.error = error.map(str::to_owned);
                app.failed_action = error.map(|_| Operation::Next);
                let element = widget::container(app.content())
                    .width(360)
                    .class(cosmic::theme::Container::Background);
                let path = dir.join(format!("{name}-{theme_name}.png"));
                snapshot(element.into(), &theme, &path);
            }
        }
    }
}
