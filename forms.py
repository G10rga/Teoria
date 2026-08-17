from __future__ import annotations

import re

from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, Length, ValidationError

from models import User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_ა-ჰ]{3,32}$")


def _unique_username(_, field) -> None:
    name = field.data.strip()
    if User.query.filter(User.username == name).one_or_none():
        raise ValidationError("ეს სახელი უკვე გამოყენებულია.")


def _unique_email(_, field) -> None:
    email = field.data.strip().lower()
    if User.query.filter(User.email == email).one_or_none():
        raise ValidationError("ეს ელფოსტა უკვე გამოყენებულია.")


def _username_format(_, field) -> None:
    if not USERNAME_RE.match(field.data.strip()):
        raise ValidationError("3–32 სიმბოლო: ლათინური/ქართული ასოები, ციფრები, _.")


class RegisterForm(FlaskForm):
    username = StringField("სახელი", validators=[
        DataRequired(message="შეიყვანეთ სახელი."),
        Length(min=3, max=32),
        _username_format,
        _unique_username,
    ])
    email = StringField("ელფოსტა", validators=[
        DataRequired(message="შეიყვანეთ ელფოსტა."),
        Email(message="ელფოსტა არასწორია."),
        Length(max=255),
        _unique_email,
    ])
    password = PasswordField("პაროლი", validators=[
        DataRequired(message="შეიყვანეთ პაროლი."),
        Length(min=8, max=128, message="პაროლი მინიმუმ 8 სიმბოლო."),
    ])
    confirm = PasswordField("გაიმეორეთ პაროლი", validators=[
        DataRequired(message="გაიმეორეთ პაროლი."),
        EqualTo("password", message="პაროლები არ ემთხვევა."),
    ])


class LoginForm(FlaskForm):
    username = StringField("სახელი ან ელფოსტა", validators=[
        DataRequired(message="შეიყვანეთ სახელი ან ელფოსტა."),
    ])
    password = PasswordField("პაროლი", validators=[
        DataRequired(message="შეიყვანეთ პაროლი."),
    ])


class TicketEditForm(FlaskForm):
    question = TextAreaField("კითხვა", validators=[
        DataRequired(message="შეიყვანეთ კითხვა."),
        Length(max=4000),
    ])
    answer_1 = TextAreaField("პასუხი 1")
    answer_2 = TextAreaField("პასუხი 2")
    answer_3 = TextAreaField("პასუხი 3")
    answer_4 = TextAreaField("პასუხი 4")
    correct_index = SelectField(
        "სწორი პასუხი",
        choices=[("", "—"), ("0", "1"), ("1", "2"), ("2", "3"), ("3", "4")],
    )
    explanation = TextAreaField("განმარტება")
    image = StringField("სურათის გზა")

    def answers_list(self) -> list[str]:
        out = []
        for field in (self.answer_1, self.answer_2, self.answer_3, self.answer_4):
            text = (field.data or "").strip()
            if text:
                out.append(text)
        return out
