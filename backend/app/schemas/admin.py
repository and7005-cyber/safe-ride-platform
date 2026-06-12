from typing import Literal

from pydantic import BaseModel, Field

TripSession = Literal["morning", "afternoon", "adhoc", "staff"]
AttendanceStatus = Literal["riding", "absent", "alternative_transport"]
TripPassengerStatus = Literal[
    "pending",
    "boarded",
    "dropped",
    "absent_admin",
    "absent_driver",
    "alternative_transport",
]


class CreateBusRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    label: str
    registration_number: str | None = Field(default=None, alias="registrationNumber")


class CreateStudentRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    grade_level: str | None = Field(default=None, alias="gradeLevel")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class UpdateStudentRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    grade_level: str | None = Field(default=None, alias="gradeLevel")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class StudentSetupStudentRequest(BaseModel):
    full_name: str = Field(alias="fullName")
    grade_level: str | None = Field(default=None, alias="gradeLevel")
    home_address: str = Field(alias="homeAddress")
    home_location_note: str | None = Field(default=None, alias="homeLocationNote")


class StudentSetupParentContactRequest(BaseModel):
    contact_1_name: str = Field(alias="contact1Name")
    contact_1_phone: str = Field(alias="contact1Phone")
    contact_1_relationship: str = Field(alias="contact1Relationship")
    contact_2_name: str | None = Field(default=None, alias="contact2Name")
    contact_2_phone: str | None = Field(default=None, alias="contact2Phone")
    contact_2_relationship: str | None = Field(default=None, alias="contact2Relationship")


class StudentSetupTripAssignmentRequest(BaseModel):
    trip_id: str = Field(alias="tripId")
    sequence_position: int = Field(alias="sequencePosition")
    estimated_minutes_from_start: int = Field(alias="estimatedMinutesFromStart")


class CreateStudentSetupRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student: StudentSetupStudentRequest
    parent_contact: StudentSetupParentContactRequest | None = Field(default=None, alias="parentContact")
    create_parent_link: bool = Field(default=False, alias="createParentLink")
    trip_assignment: StudentSetupTripAssignmentRequest | None = Field(default=None, alias="tripAssignment")


class CreateDriverRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    full_name: str = Field(alias="fullName")
    phone: str | None = None
    default_bus_id: str | None = Field(default=None, alias="defaultBusId")
    pin: str


class CreateParentContactRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    contact_1_name: str = Field(alias="contact1Name")
    contact_1_phone: str = Field(alias="contact1Phone")
    contact_1_relationship: str = Field(alias="contact1Relationship")
    contact_2_name: str | None = Field(default=None, alias="contact2Name")
    contact_2_phone: str | None = Field(default=None, alias="contact2Phone")
    contact_2_relationship: str | None = Field(default=None, alias="contact2Relationship")


class CreateParentLinkRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    token: str


class CreateTripRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    bus_id: str = Field(alias="busId")
    driver_id: str | None = Field(default=None, alias="driverId")
    name: str
    session: TripSession
    service_date: str = Field(alias="serviceDate")
    scheduled_start: str = Field(alias="scheduledStart")


class CreateTripPassengerRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    trip_id: str = Field(alias="tripId")
    student_id: str = Field(alias="studentId")
    sequence_position: int = Field(alias="sequencePosition")
    estimated_minutes_from_start: int = Field(alias="estimatedMinutesFromStart")


class MarkDailyAttendanceRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    student_id: str = Field(alias="studentId")
    attendance_date: str = Field(alias="attendanceDate")
    status: AttendanceStatus
    note: str | None = None


class CorrectTripPassengerStatusRequest(BaseModel):
    school_id: str = Field(alias="schoolId")
    trip_passenger_id: str = Field(alias="tripPassengerId")
    corrected_status: TripPassengerStatus = Field(alias="correctedStatus")
    reason: str
