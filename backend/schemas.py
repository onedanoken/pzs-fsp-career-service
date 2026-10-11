from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .taxonomy import SKILLS, ROLES

class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)

class Login(StrictModel):
    username:str|None=Field(default=None,min_length=3,max_length=40)
    email:str|None=Field(default=None,min_length=3,max_length=160)
    password:str=Field(min_length=1,max_length=200)

class DemoLogin(StrictModel):
    account:Literal["u_employer","u_employer2","u_anna","u_timur","u_admin"]

class Registration(StrictModel):
    name:str=Field(min_length=2,max_length=80)
    username:str|None=Field(default=None,pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,39}$")
    email:str|None=Field(default=None,pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",max_length=160)
    password:str=Field(min_length=8,max_length=200)
    role:Literal["candidate","employer"]
    company:str|None=Field(default=None,min_length=2,max_length=100)
    processing_consent:bool=False
    @model_validator(mode="after")
    def company_required(self):
        if self.role=="employer" and not self.company:
            raise ValueError("Укажите название компании")
        return self

class ProfileUpdate(StrictModel):
    name:str=Field(min_length=2,max_length=80)
    title:str=Field(min_length=2,max_length=120)
    role:str
    city:str=Field(max_length=80)
    experience_years:float|None=Field(default=None,ge=0,le=50)
    salary_min:int|None=Field(default=None,ge=0,le=5_000_000)
    skills:list[str]=Field(max_length=30)
    bio:str=Field(max_length=2000)
    visible:bool
    looking:bool=True
    relocate:bool=False
    work_modes:list[Literal["remote","hybrid","office"]]=Field(min_length=1,max_length=3)
    version:int=Field(ge=1)
    contacts:dict[str,str]=Field(default_factory=dict,max_length=5)
    soft_skills:list[str]=Field(default_factory=list,max_length=20)
    career_history:list[dict[str,str]]=Field(default_factory=list,max_length=20)
    industry:str=Field(default='IT',max_length=80)
    declared_grade:Literal['Intern','Junior','Middle','Senior']|None=None
    @field_validator('soft_skills','career_history')
    @classmethod
    def bounded_profile_text(cls,value):
        for item in value:
            if isinstance(item,str) and len(item)>120:raise ValueError('Слишком длинный софт-скилл')
            if isinstance(item,dict) and (len(item)>6 or any(len(k)>50 or len(v)>1000 for k,v in item.items())):raise ValueError('Слишком длинная запись опыта')
        return value
    @field_validator('contacts')
    @classmethod
    def valid_contacts(cls,value):
        if set(value)-{'email','phone','telegram','website'} or any(len(v)>200 for v in value.values()):
            raise ValueError('Недопустимые поля контактов')
        return value
    @field_validator("role")
    @classmethod
    def valid_role(cls,value):
        if value not in ROLES:
            raise ValueError("Неизвестная роль")
        return value
    @field_validator("skills")
    @classmethod
    def valid_skills(cls,values):
        if any(s not in SKILLS for s in values):
            raise ValueError("Неизвестный навык")
        return list(dict.fromkeys(values))

class VacancyInput(StrictModel):
    title:str=Field(min_length=3,max_length=140)
    description:str=Field(min_length=20,max_length=8000)
    role:str
    required_skills:list[str]=Field(min_length=1,max_length=25)
    optional_skills:list[str]=Field(default_factory=list,max_length=25)
    salary_min:int=Field(ge=1,le=5_000_000)
    salary_max:int=Field(ge=1,le=5_000_000)
    salary_type:Literal["net","gross"]="net"
    experience_min:float=Field(default=0,ge=0,le=50)
    work_mode:Literal["remote","hybrid","office"]="remote"
    city:str|None=Field(default=None,max_length=80)
    valid_days:int=Field(default=30,ge=1,le=90)
    @field_validator("role")
    @classmethod
    def valid_role(cls,value):
        if value not in ROLES:
            raise ValueError("Неизвестная роль")
        return value
    @model_validator(mode="after")
    def validate_requirements(self):
        if self.salary_max<self.salary_min:
            raise ValueError("Максимальная зарплата ниже минимальной")
        if any(s not in SKILLS for s in self.required_skills+self.optional_skills):
            raise ValueError("Неизвестный навык")
        if set(self.required_skills)&set(self.optional_skills):
            raise ValueError("Навык не может быть одновременно обязательным и желательным")
        if self.work_mode!="remote" and not self.city:
            raise ValueError("Укажите город")
        return self

class ExtractInput(StrictModel):
    text:str=Field(min_length=3,max_length=10000)

class OfferInput(StrictModel):
    candidate_id:str=Field(min_length=1,max_length=100)
    vacancy_id:str=Field(min_length=1,max_length=100)
    salary_min:int=Field(ge=1,le=5_000_000)
    salary_max:int=Field(ge=1,le=5_000_000)
    salary_type:Literal["net","gross"]="net"
    message:str=Field(min_length=5,max_length=2000)
    idempotency_key:str=Field(min_length=12,max_length=100)
    @model_validator(mode="after")
    def range_valid(self):
        if self.salary_max<self.salary_min:
            raise ValueError("Максимальная зарплата ниже минимальной")
        return self

class OfferResponse(StrictModel):
    action:Literal["accepted","declined","clarification","withdrawn","replied"]
    message:str=Field(default="",max_length=1000)
    version:int=Field(ge=1)
    @model_validator(mode="after")
    def question_or_reply(self):
        if self.action in {"clarification","replied"} and not self.message:
            raise ValueError("Напишите сообщение")
        return self

class AchievementState(StrictModel):
    active:bool
    version:int=Field(ge=1)

class BookmarkInput(StrictModel):
    entity_type:Literal["candidate","vacancy"]
    entity_id:str=Field(max_length=100)
    saved:bool

class ImportInput(StrictModel):
    query:str=Field(default="разработчик",min_length=2,max_length=100)
    limit:int=Field(default=10,ge=1,le=20)
